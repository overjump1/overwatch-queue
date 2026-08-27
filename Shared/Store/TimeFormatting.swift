import Foundation

/// Queue times are read at a glance, often on a wrist, often mid-fight. These formatters
/// exist so every surface renders a duration identically.
public enum QueueTime {
    /// `3:07`, or `1:02:14` past an hour. Monospaced digits are applied at the view layer.
    public static func clock(_ interval: TimeInterval) -> String {
        let total = max(0, Int(interval.rounded()))
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0
            ? String(format: "%d:%02d:%02d", h, m, s)
            : String(format: "%d:%02d", m, s)
    }

    /// `3m 07s` — for spots where a bare colon-separated number reads as a score.
    public static func compact(_ interval: TimeInterval) -> String {
        let total = max(0, Int(interval.rounded()))
        if total < 60 { return "\(total)s" }
        let m = total / 60, s = total % 60
        return s == 0 ? "\(m)m" : "\(m)m \(String(format: "%02d", s))s"
    }

    /// Phrasing for an estimate, which is always approximate and shouldn't pretend otherwise.
    public static func estimate(_ interval: TimeInterval?) -> String {
        guard let interval else { return "Estimating…" }
        return "~\(compact(interval))"
    }
}
