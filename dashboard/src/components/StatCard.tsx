export function StatCard({
  label,
  value,
  sub,
  icon,
}: {
  label: string
  value: string
  sub?: string
  icon?: string
}) {
  return (
    <div className="bg-ctp-base border border-ctp-surface0 p-3 rounded-xl relative overflow-hidden group">
      {icon && (
        <div className="absolute right-0 top-0 p-3 opacity-10 group-hover:opacity-20 transition-opacity">
          <span className="material-symbols-outlined text-4xl">{icon}</span>
        </div>
      )}
      <p className="text-[10px] text-ctp-subtext0 uppercase font-bold tracking-wider mb-1">{label}</p>
      <p className="text-lg font-bold text-white leading-none">{value}</p>
      {sub && <p className="text-xs text-ctp-subtext0 mt-1">{sub}</p>}
    </div>
  )
}
