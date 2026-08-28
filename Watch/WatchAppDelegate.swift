import WatchKit

/// The watchOS counterpart to `iOS/AppDelegate.swift` — forwards the two APNs callbacks
/// `WKApplicationDelegate` only delivers to a delegate, into `WatchModel`.
final class WatchAppDelegate: NSObject, WKApplicationDelegate {
    var onDeviceToken: ((Data) -> Void)?
    var onRemoteNotification: (() async -> Void)?

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
}
