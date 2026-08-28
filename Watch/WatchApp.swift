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
                    model.start()
                }
        }
    }
}
