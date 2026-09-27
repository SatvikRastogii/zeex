/** Status as plain text: "awaiting_approval" -> "[AWAITING APPROVAL]". */
export function statusTag(status: string): string {
  return `[${status.replace(/_/g, " ").toUpperCase()}]`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-05" -> "05 Oct 2026" (calendar date, no timezone shift). */
export function formatDate(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d} ${MONTHS[Number(m) - 1]} ${y}`;
}

/** UTC timestamp -> "25 Sep 2026 14:05 IST". */
export function formatIST(ts: string): string {
  const shifted = new Date(new Date(ts).getTime() + 330 * 60_000);
  const hh = String(shifted.getUTCHours()).padStart(2, "0");
  const mm = String(shifted.getUTCMinutes()).padStart(2, "0");
  return `${formatDate(shifted.toISOString())} ${hh}:${mm} IST`;
}

function indianGroup(digits: string): string {
  if (digits.length <= 3) return digits;
  const head = digits.slice(0, -3);
  const tail = digits.slice(-3);
  return `${head.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${tail}`;
}

/** Integer paise -> "₹11,400.00". */
export function formatINR(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const p = String(abs % 100).padStart(2, "0");
  return `${sign}₹${indianGroup(String(rupees))}.${p}`;
}

export function newRef(): string {
  return crypto.randomUUID().replace(/-/g, "");
}
