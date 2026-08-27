import Foundation
#if canImport(WidgetKit)
import WidgetKit
#endif

/// The last known snapshot, in the app group container so widgets can read it.
///
/// Live Activities get their data through ActivityKit; this exists for the Home Screen,
/// StandBy and Smart Stack widgets, which run in their own process and would otherwise
/// have nothing to show.
public enum SharedState {
    public static let appGroup = "group.com.tomerady.OverwatchQueue"
    private static let key = "latest-snapshot"

    private static var defaults: UserDefaults {
        // Falls back to standard defaults if the app group isn't provisioned — the app
        // still works, the widget just won't see anything.
        UserDefaults(suiteName: appGroup) ?? .standard
    }

    public static func write(_ snapshot: QueueSnapshot) {
        guard let data = try? Wire.encode(snapshot) else { return }
        defaults.set(data, forKey: key)
        #if canImport(WidgetKit)
        WidgetCenter.shared.reloadAllTimelines()
        #endif
    }

    public static func read() -> QueueSnapshot? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? Wire.decode(QueueSnapshot.self, from: data)
    }
}
