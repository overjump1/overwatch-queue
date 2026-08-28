import SwiftUI
import WatchKit

@main
struct OverwatchQueueWatchApp: App {
    @State private var model = WatchModel()
    @WKApplicationDelegateAdaptor(WatchAppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            WatchRootView()
                .environment(model)
                .environment(model.store)
                .task {
                    appDelegate.onDeviceToken = { model.didReceive(deviceToken: $0) }
                    appDelegate.onRemoteNotification = {
                        await withCheckedContinuation { continuation in
                            model.handleBackgroundPush { continuation.resume() }
                        }
                    }
                    appDelegate.onNotificationTap = { model.handleUserOpened() }
                    appDelegate.shouldPresentWhileForeground = {
                        model.store.transport?.status.isLive != true
                    }
                    model.start()
                }
                // The mirrored Live Activity's tap target. Handling it here is what lets
                // the wrist open itself rather than offering to open the iPhone — the URL
                // is the same one the phone claims, and each device answers for itself.
                // There's nothing to route to: `WatchRootView` already draws the current
                // phase, so arriving is the whole job.
                .onOpenURL { url in
                    if case .open = DeepLink.parse(url) { model.handleUserOpened() }
                }
        }
    }
}
