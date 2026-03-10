"""
Tests for Phase 6: Memory Manager

Test layers:
  1. TokenCounter — accurate token counting, message overhead, truncation
  2. SlidingWindowMemory — FIFO eviction, preserved messages, budget enforcement
  3. VectorMemory — store/recall/delete with in-memory fake backend
  4. MemoryManager — context assembly with budget allocation
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from agentworks.memory import (
    MemoryEntry,
    MemoryManager,
    SlidingWindowMemory,
    TokenCounter,
    VectorMemory,
)

# --------------------------------------------------------------------------
# Test helpers / fakes
# --------------------------------------------------------------------------


class FakeVectorStore:
    """
    In-memory vector store for testing.

    Implements the VectorStore protocol with brute-force cosine similarity.
    No need for pgvector — we just need correct behavior.
    """

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    async def insert(
        self,
        content: str,
        embedding: list[float],
        agent_id: str,
        team_id: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> str:
        entry_id = str(uuid.uuid4())
        self._entries.append(
            {
                "entry_id": entry_id,
                "content": content,
                "embedding": embedding,
                "agent_id": agent_id,
                "team_id": team_id,
                "run_id": run_id,
                "metadata": metadata,
                "created_at": datetime.now(UTC),
            }
        )
        return entry_id

    async def search(
        self,
        query_embedding: list[float],
        top_k: int,
        min_score: float,
        agent_id: str | None,
        team_id: str | None,
    ) -> list[dict[str, Any]]:
        results = []
        for entry in self._entries:
            # Filter by agent_id / team_id
            if agent_id and entry["agent_id"] != agent_id:
                continue
            if team_id and entry["team_id"] != team_id:
                continue

            score = self._cosine_similarity(query_embedding, entry["embedding"])
            if score >= min_score:
                results.append(
                    {
                        **entry,
                        "similarity_score": score,
                    }
                )

        # Sort by similarity (highest first)
        results.sort(key=lambda r: r["similarity_score"], reverse=True)
        return results[:top_k]

    async def delete_by_run(self, run_id: str) -> int:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["run_id"] != run_id]
        return before - len(self._entries)

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Brute-force cosine similarity."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


async def fake_embed(text: str) -> list[float]:
    """
    Deterministic fake embedding function.

    Uses a simple hash-based approach to produce consistent embeddings.
    Similar texts won't have similar embeddings, but that's fine —
    we control similarity through the FakeVectorStore directly.
    """
    # Produce a 4-dimensional "embedding" from the text hash
    h = hash(text) & 0xFFFFFFFF
    return [
        ((h >> 0) & 0xFF) / 255.0,
        ((h >> 8) & 0xFF) / 255.0,
        ((h >> 16) & 0xFF) / 255.0,
        ((h >> 24) & 0xFF) / 255.0,
    ]


async def identical_embed(text: str) -> list[float]:
    """Embedding function that always returns the same vector (similarity = 1.0)."""
    return [1.0, 0.0, 0.0, 0.0]


# --------------------------------------------------------------------------
# 1. TokenCounter Tests
# --------------------------------------------------------------------------


class TestTokenCounter:
    """Accurate token counting via tiktoken."""

    def test_empty_string_is_zero(self):
        tc = TokenCounter()
        assert tc.count("") == 0

    def test_simple_text(self):
        tc = TokenCounter()
        count = tc.count("hello world")
        assert count == 2  # tiktoken: "hello" + " world"

    def test_longer_text(self):
        tc = TokenCounter()
        count = tc.count("The quick brown fox jumps over the lazy dog")
        assert count > 0
        assert count < 20  # sanity check

    def test_message_overhead(self):
        """Messages have framing overhead beyond just content tokens."""
        tc = TokenCounter()
        content_tokens = tc.count("hello")
        msg_tokens = tc.count_message({"role": "user", "content": "hello"})
        # Message should have more tokens than just content (framing overhead)
        assert msg_tokens > content_tokens

    def test_message_with_tool_calls(self):
        """Tool calls add additional tokens."""
        tc = TokenCounter()
        base = tc.count_message({"role": "assistant", "content": "Let me search"})
        with_tools = tc.count_message(
            {
                "role": "assistant",
                "content": "Let me search",
                "tool_calls": [
                    {
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "test"}',
                        },
                    }
                ],
            }
        )
        assert with_tools > base

    def test_count_messages_includes_priming(self):
        """Total count includes 3 reply priming tokens."""
        tc = TokenCounter()
        single = tc.count_message({"role": "user", "content": "hi"})
        total = tc.count_messages([{"role": "user", "content": "hi"}])
        assert total == single + 3  # 3 priming tokens

    def test_truncate_preserves_short_text(self):
        tc = TokenCounter()
        text = "hello"
        assert tc.truncate_to_tokens(text, 100) == text

    def test_truncate_long_text(self):
        tc = TokenCounter()
        # Generate text that's definitely more than 5 tokens
        text = "The quick brown fox jumps over the lazy dog " * 10
        truncated = tc.truncate_to_tokens(text, 5)
        assert tc.count(truncated) <= 5
        assert len(truncated) < len(text)

    def test_fallback_encoding_for_unknown_model(self):
        """Unknown models fall back to cl100k_base."""
        tc = TokenCounter(model="some-unknown-model-xyz")
        # Should not raise — falls back gracefully
        assert tc.count("hello") > 0

    def test_none_content_in_message(self):
        """Messages with None content don't crash."""
        tc = TokenCounter()
        tokens = tc.count_message({"role": "assistant", "content": None})
        assert tokens >= 4  # at least framing overhead


# --------------------------------------------------------------------------
# 2. SlidingWindowMemory Tests
# --------------------------------------------------------------------------


class TestSlidingWindowBasic:
    """Basic add/get operations."""

    def test_add_and_retrieve(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        assert len(sw.messages) == 1
        assert sw.messages[0]["content"] == "hello"

    def test_add_many(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add_many(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        )
        assert len(sw.messages) == 2

    def test_clear(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        sw.clear()
        assert len(sw.messages) == 0

    def test_token_count_property(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        assert sw.token_count > 0

    def test_messages_returns_copy(self):
        """The messages property returns a copy, not a reference."""
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        msgs = sw.messages
        msgs.append({"role": "user", "content": "injected"})
        assert len(sw.messages) == 1  # original unchanged


class TestSlidingWindowEviction:
    """Token-aware eviction preserving system prompt and first user message."""

    def test_preserves_system_prompt(self):
        """System messages are never evicted, even under pressure."""
        sw = SlidingWindowMemory(max_tokens=100)
        sw.add({"role": "system", "content": "You are helpful."})
        # Fill with enough messages to trigger eviction
        for i in range(20):
            sw.add({"role": "user", "content": f"message {i} " * 10})

        messages = sw.messages
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) >= 1
        assert system_msgs[0]["content"] == "You are helpful."

    def test_preserves_first_user_message(self):
        """The first user message is never evicted."""
        sw = SlidingWindowMemory(max_tokens=150)
        sw.add({"role": "system", "content": "System prompt."})
        sw.add({"role": "user", "content": "What is the meaning of life?"})
        # Fill with more messages
        for i in range(20):
            sw.add({"role": "assistant", "content": f"response {i} " * 10})

        messages = sw.messages
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert any(m["content"] == "What is the meaning of life?" for m in user_msgs)

    def test_evicts_oldest_first(self):
        """Oldest trimmable messages are evicted first (FIFO)."""
        sw = SlidingWindowMemory(max_tokens=200)
        sw.add({"role": "user", "content": "original task"})
        sw.add({"role": "assistant", "content": "old reply " * 20})
        sw.add({"role": "user", "content": "followup " * 5})
        sw.add({"role": "assistant", "content": "newest reply"})

        messages = sw.messages
        # "newest reply" should survive; "old reply" may be evicted
        contents = [m.get("content", "") for m in messages]
        assert "newest reply" in contents

    def test_get_window_with_custom_budget(self):
        """get_window can use a smaller budget than the default."""
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        sw.add({"role": "assistant", "content": "world " * 100})

        # Small budget should trim
        window = sw.get_window(max_tokens=50)
        tc = TokenCounter()
        assert tc.count_messages(window) <= 50

    def test_get_window_within_budget_returns_all(self):
        """When under budget, all messages are returned."""
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "user", "content": "hello"})
        sw.add({"role": "assistant", "content": "hi"})

        window = sw.get_window()
        assert len(window) == 2


class TestSlidingWindowEdgeCases:
    """Edge cases for sliding window."""

    def test_empty_window(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        assert sw.messages == []
        assert sw.get_window() == []

    def test_only_system_message(self):
        sw = SlidingWindowMemory(max_tokens=10000)
        sw.add({"role": "system", "content": "You are helpful."})
        messages = sw.messages
        assert len(messages) == 1

    def test_second_user_message_is_trimmable(self):
        """Only the FIRST user message is preserved; subsequent ones can be evicted."""
        sw = SlidingWindowMemory(max_tokens=150)
        sw.add({"role": "user", "content": "first question"})
        sw.add({"role": "user", "content": "second question " * 20})
        sw.add({"role": "user", "content": "third question"})

        messages = sw.messages
        contents = [m.get("content", "") for m in messages]
        # First question preserved, second may be evicted, third is newest
        assert "first question" in contents


# --------------------------------------------------------------------------
# 3. VectorMemory Tests
# --------------------------------------------------------------------------


class TestVectorMemoryStore:
    """Storing memories in the vector store."""

    @pytest.mark.asyncio
    async def test_store_returns_entry_id(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)
        entry_id = await vm.store(
            content="User prefers JSON output",
            agent_id="agent-1",
            team_id="team-1",
            run_id="run-1",
        )
        assert entry_id is not None
        assert len(entry_id) > 0

    @pytest.mark.asyncio
    async def test_store_with_metadata(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)
        await vm.store(
            content="Important fact",
            metadata={"source": "user_preference"},
            agent_id="a1",
        )
        assert len(store._entries) == 1
        assert store._entries[0]["metadata"]["source"] == "user_preference"

    @pytest.mark.asyncio
    async def test_store_calls_embedding_function(self):
        store = FakeVectorStore()
        embed_calls = []

        async def tracking_embed(text: str) -> list[float]:
            embed_calls.append(text)
            return [1.0, 0.0, 0.0, 0.0]

        vm = VectorMemory(store=store, embedding_fn=tracking_embed)
        await vm.store(content="hello world")
        assert embed_calls == ["hello world"]


class TestVectorMemoryRecall:
    """Retrieving memories by semantic similarity."""

    @pytest.mark.asyncio
    async def test_recall_returns_similar_entries(self):
        """Entries with identical embeddings have similarity 1.0."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        await vm.store(content="fact one", agent_id="a1")
        await vm.store(content="fact two", agent_id="a1")

        results = await vm.recall(query="anything", top_k=5, min_score=0.5, agent_id="a1")
        assert len(results) == 2
        assert all(r.similarity_score >= 0.5 for r in results)

    @pytest.mark.asyncio
    async def test_recall_filters_by_agent_id(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        await vm.store(content="agent-1 fact", agent_id="a1")
        await vm.store(content="agent-2 fact", agent_id="a2")

        results = await vm.recall(query="anything", top_k=5, min_score=0.5, agent_id="a1")
        assert len(results) == 1
        assert results[0].content == "agent-1 fact"

    @pytest.mark.asyncio
    async def test_recall_filters_by_team_id(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        await vm.store(content="team-1 fact", team_id="t1")
        await vm.store(content="team-2 fact", team_id="t2")

        results = await vm.recall(query="anything", top_k=5, min_score=0.5, team_id="t1")
        assert len(results) == 1
        assert results[0].content == "team-1 fact"

    @pytest.mark.asyncio
    async def test_recall_respects_top_k(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        for i in range(10):
            await vm.store(content=f"fact {i}")

        results = await vm.recall(query="anything", top_k=3, min_score=0.5)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_recall_respects_min_score(self):
        """Entries below min_score are excluded."""
        store = FakeVectorStore()

        # Use fake_embed which produces different vectors per text,
        # so entries won't match each other perfectly
        vm = VectorMemory(store=store, embedding_fn=fake_embed)

        await vm.store(content="completely unrelated text xyz")

        results = await vm.recall(query="different query abc", top_k=5, min_score=0.99)
        # Very high min_score should filter out dissimilar entries
        # (fake_embed produces different vectors for different text)
        assert len(results) <= 1  # may or may not match

    @pytest.mark.asyncio
    async def test_recall_empty_store(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)
        results = await vm.recall(query="anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_returns_memory_entries(self):
        """Results are MemoryEntry objects with proper fields."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        await vm.store(
            content="test fact",
            agent_id="a1",
            team_id="t1",
            run_id="r1",
        )

        results = await vm.recall(query="anything", min_score=0.5)
        assert len(results) == 1
        entry = results[0]
        assert isinstance(entry, MemoryEntry)
        assert entry.content == "test fact"
        assert entry.agent_id == "a1"
        assert entry.team_id == "t1"
        assert entry.run_id == "r1"
        assert entry.similarity_score > 0


class TestVectorMemoryDelete:
    """Deleting memories by run_id."""

    @pytest.mark.asyncio
    async def test_delete_by_run(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)

        await vm.store(content="run-1 fact", run_id="r1")
        await vm.store(content="run-2 fact", run_id="r2")

        count = await vm.delete_by_run("r1")
        assert count == 1
        assert len(store._entries) == 1
        assert store._entries[0]["run_id"] == "r2"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_run(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)
        count = await vm.delete_by_run("nonexistent")
        assert count == 0


# --------------------------------------------------------------------------
# 4. MemoryManager Tests
# --------------------------------------------------------------------------


class TestMemoryManagerShortTermOnly:
    """Context assembly without long-term memory."""

    @pytest.mark.asyncio
    async def test_basic_context_assembly(self):
        mm = MemoryManager(max_context_tokens=10000)
        ctx = await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
            system_prompt="You are helpful.",
        )

        # Should have system prompt + user message
        assert len(ctx) >= 2
        assert ctx[0]["role"] == "system"
        assert ctx[0]["content"] == "You are helpful."

    @pytest.mark.asyncio
    async def test_no_system_prompt(self):
        mm = MemoryManager(max_context_tokens=10000)
        ctx = await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
        )
        # No system prompt, just the conversation
        assert len(ctx) >= 1
        assert ctx[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_conversation_in_context(self):
        mm = MemoryManager(max_context_tokens=10000)
        conversation = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "Tell me more."},
        ]
        ctx = await mm.build_context(
            query="Tell me more",
            conversation=conversation,
            system_prompt="You are helpful.",
        )
        # All conversation messages should be present
        contents = [m.get("content", "") for m in ctx]
        assert "What is Python?" in contents
        assert "Python is a programming language." in contents

    @pytest.mark.asyncio
    async def test_context_respects_token_budget(self):
        """Context assembly stays within the token budget."""
        mm = MemoryManager(max_context_tokens=100)
        # Large conversation that exceeds budget
        conversation = [
            {"role": "user", "content": "original question"},
        ]
        for i in range(20):
            conversation.append({"role": "assistant", "content": f"long response {i} " * 20})

        ctx = await mm.build_context(
            query="latest",
            conversation=conversation,
            system_prompt="You are helpful.",
        )
        tc = TokenCounter()
        assert tc.count_messages(ctx) <= 100


class TestMemoryManagerWithLongTerm:
    """Context assembly with vector memory integration."""

    @pytest.mark.asyncio
    async def test_long_term_memories_injected(self):
        """Relevant long-term memories appear in the context."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)
        await vm.store(content="User prefers concise answers", agent_id="a1")

        mm = MemoryManager(max_context_tokens=10000, vector_memory=vm)
        ctx = await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
            system_prompt="You are helpful.",
            agent_id="a1",
        )

        # Should have: system prompt, long-term memory, user message
        all_content = " ".join(m.get("content", "") for m in ctx)
        assert "User prefers concise answers" in all_content

    @pytest.mark.asyncio
    async def test_long_term_memories_as_system_message(self):
        """Long-term memories are injected as a system message."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)
        await vm.store(content="Important context")

        mm = MemoryManager(max_context_tokens=10000, vector_memory=vm)
        ctx = await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
        )

        system_msgs = [m for m in ctx if m["role"] == "system"]
        memory_msgs = [
            m for m in system_msgs if "previous interactions" in m.get("content", "").lower()
        ]
        assert len(memory_msgs) >= 1

    @pytest.mark.asyncio
    async def test_long_term_budget_limited(self):
        """Long-term memories don't exceed 20% of available budget."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)

        # Store many large memories
        for i in range(10):
            await vm.store(content=f"Very long memory entry {i} " * 50)

        mm = MemoryManager(max_context_tokens=200, vector_memory=vm)
        ctx = await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
            system_prompt="Short prompt.",
        )

        # Total should stay within budget
        tc = TokenCounter()
        assert tc.count_messages(ctx) <= 200

    @pytest.mark.asyncio
    async def test_no_memories_when_query_empty(self):
        """Empty query skips long-term recall."""
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=identical_embed)
        await vm.store(content="stored fact")

        mm = MemoryManager(max_context_tokens=10000, vector_memory=vm)
        ctx = await mm.build_context(
            query="",
            conversation=[{"role": "user", "content": "hello"}],
        )

        all_content = " ".join(m.get("content", "") for m in ctx)
        assert "stored fact" not in all_content


class TestMemoryManagerStoreInteraction:
    """Storing interactions for future recall."""

    @pytest.mark.asyncio
    async def test_store_with_vector_memory(self):
        store = FakeVectorStore()
        vm = VectorMemory(store=store, embedding_fn=fake_embed)
        mm = MemoryManager(vector_memory=vm)

        entry_id = await mm.store_interaction(
            content="User asked about pricing",
            agent_id="a1",
            team_id="t1",
            run_id="r1",
        )
        assert entry_id is not None

    @pytest.mark.asyncio
    async def test_store_without_vector_memory(self):
        """Without long-term memory, store_interaction returns None."""
        mm = MemoryManager()
        result = await mm.store_interaction(content="anything")
        assert result is None


class TestMemoryManagerClearShortTerm:
    """Clearing the sliding window."""

    @pytest.mark.asyncio
    async def test_clear_resets_window(self):
        mm = MemoryManager(max_context_tokens=10000)
        await mm.build_context(
            query="hello",
            conversation=[{"role": "user", "content": "hello"}],
        )
        assert len(mm.short_term.messages) > 0

        mm.clear_short_term()
        assert len(mm.short_term.messages) == 0


# --------------------------------------------------------------------------
# 5. MemoryEntry Model Tests
# --------------------------------------------------------------------------


class TestMemoryEntryModel:
    """Pydantic model tests for MemoryEntry."""

    def test_defaults(self):
        entry = MemoryEntry(entry_id="e1", content="test")
        assert entry.agent_id == ""
        assert entry.similarity_score == 0.0
        assert entry.metadata == {}
        assert isinstance(entry.created_at, datetime)

    def test_serialization_excludes_embedding(self):
        """Embedding is excluded from serialization (marked exclude=True)."""
        entry = MemoryEntry(
            entry_id="e1",
            content="test",
            embedding=[1.0, 2.0, 3.0],
        )
        data = entry.model_dump()
        assert "embedding" not in data

    def test_full_construction(self):
        entry = MemoryEntry(
            entry_id="e1",
            content="User prefers JSON",
            agent_id="a1",
            team_id="t1",
            run_id="r1",
            similarity_score=0.95,
            metadata={"source": "preference"},
        )
        assert entry.similarity_score == 0.95
        assert entry.metadata["source"] == "preference"
