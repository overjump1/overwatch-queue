import UserNotifications
import WatchKit

/// The watchOS counterpart to `iOS/AppDelegate.swift` — forwards the push callbacks
/// `WKApplicationDelegate` and `UNUserNotificationCenter` only deliver to a delegate,
/// into `WatchModel`.
final class WatchAppDelegate: NSObject, WKApplicationDelegate, UNUserNotificationCenterDelegate {
    var onDeviceToken: ((Data) -> Void)?
    var onRemoteNotification: (() async -> Void)?

    /// The player tapped a notification on the wrist. Buffered the same way and for the
    /// same reason as on the phone — see `iOS/AppDelegate.swift`.
    var onNotificationTap: (() -> Void)? {
        didSet {
            guard pendingTap, onNotificationTap != nil else { return }
            pendingTap = false
            onNotificationTap?()
        }
    }

    var shouldPresentWhileForeground: (() -> Bool)?

    private var pendingTap = false

    func applicationDidFinishLaunching() {
        // Before launch finishes, or a tap that started the app never reaches us.
        UNUserNotificationCenter.current().delegate = self
    }

    func didRegisterForRemoteNotifications(withDeviceToken deviceToken: Data) {
        onDeviceToken?(deviceToken)
    }

    func didFailToRegisterForRemoteNotificationsWithError(_ error: Error) {
        // Nothing to do: the watch just keeps relying on the phone relay / its own
        // socket until a future launch registers successfully.
    }

    func didReceiveRemoteNotification(_ userInfo: [AnyHashable: Any]) async -> WKBackgroundFetchResult {
        guard let onRemoteNotification else { return .noData }
        await onRemoteNotification()
        return .newData
    }

    // MARK: - UNUserNotificationCenterDelegate

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        if onNotificationTap != nil {
            onNotificationTap?()
        } else {
            pendingTap = true
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
    -> UNNotificationPresentationOptions {
        (shouldPresentWhileForeground?() ?? true) ? [.banner, .sound, .list] : [.list]
    }
}
