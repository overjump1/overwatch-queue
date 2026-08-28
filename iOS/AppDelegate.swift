import UIKit
import UserNotifications

/// Forwards the push callbacks UIKit and `UNUserNotificationCenter` only deliver to a
/// delegate. Everything else about push — requesting permission, registering the token,
/// reacting to a background wake-up — lives on `AppModel`, which sets these closures at
/// launch.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    var onDeviceToken: ((Data) -> Void)?
    var onRemoteNotification: ((@escaping () -> Void) -> Void)?

    /// The player tapped a notification. Draining a tap that arrived before this was set
    /// is the whole reason for `pendingTap` below.
    var onNotificationTap: (() -> Void)? {
        didSet {
            guard pendingTap, onNotificationTap != nil else { return }
            pendingTap = false
            onNotificationTap?()
        }
    }

    /// Whether an alert arriving right now is news. False while the app is already showing
    /// the phase it announces.
    var shouldPresentWhileForeground: (() -> Bool)?

    /// A tap can launch the app from cold, and `didReceive` then fires before
    /// `OverwatchQueueApp`'s `.task` has wired the closures above — the tap would be
    /// dropped exactly when it mattered most. Hold it until there is something to hand
    /// it to.
    private var pendingTap = false

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Has to be set before launch finishes, or a tap that started the app is never
        // delivered at all. That rules out doing it from the SwiftUI `.task`.
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        onDeviceToken?(deviceToken)
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // Nothing to do: the app just goes on relying on its own socket until a future
        // launch registers successfully (e.g. once the user grants push permission).
    }

    func application(_ application: UIApplication,
                     didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        guard let onRemoteNotification else {
            completionHandler(.noData)
            return
        }
        onRemoteNotification { completionHandler(.newData) }
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

    /// Without this, an alert that arrives while the app is open is swallowed entirely.
    /// It still shouldn't interrupt a screen already showing the same phase — that would
    /// be the same event twice, on top of the haptic and sound `AppModel.react` plays —
    /// so in that case it goes to Notification Centre and no further.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
    -> UNNotificationPresentationOptions {
        (shouldPresentWhileForeground?() ?? true) ? [.banner, .sound, .list] : [.list]
    }
}
