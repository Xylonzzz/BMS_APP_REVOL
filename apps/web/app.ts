export type QualityStatus = "valid" | "pec_error" | "timeout" | "partial" | "stale" | "range_error"
export type SnapshotCell = { cellIndex: number; value: number; valid: boolean; quality: QualityStatus; rawFrameIds?: number[] }
export async function fetchLatest(apiBase: string): Promise<{ firmwareVersion: string; totalVoltage: { value: number }; current: { value: number }; cells: SnapshotCell[]; diagnostics: Array<{ code: string; message: string }> }> {
  const response = await fetch(`${apiBase.replace(/\/$/, "")}/api/v1/snapshots/latest`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}
