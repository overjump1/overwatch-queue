import UIKit

/// Forwards the two APNs callbacks UIKit only delivers to a `UIApplicationDelegate`.
/// Everything else about push — requesting permission, registering the token, reacting
/// to a background wake-up — lives on `AppModel`, which sets these closures at launch.
final class AppDelegate: NSObject, UIApplicationDelegate {
    var onDeviceToken: ((Data) -> Void)?
    var onRemoteNotification: ((@escaping () -> Void) -> Void)?

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
}
