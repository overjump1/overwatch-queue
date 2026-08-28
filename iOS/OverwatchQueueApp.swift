import SwiftUI

@main
struct OverwatchQueueApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .environment(model.store)
                .environment(model.catalog)
                .preferredColorScheme(.dark)
                .task {
                    appDelegate.onDeviceToken = { model.didReceive(deviceToken: $0) }
                    appDelegate.onRemoteNotification = { model.handleBackgroundPush(completion: $0) }
                    appDelegate.onNotificationTap = { model.handleUserOpened() }
                    appDelegate.shouldPresentWhileForeground = {
                        model.store.transport?.status.isLive != true
                    }
                    model.start()
                    model.registerForPushNotifications()
                }
                // Scanning the code in the system camera opens it as a URL. Handling it
                // here means the camera app is a way in, not just the in-app scanner.
                .onOpenURL { url in
                    switch DeepLink.parse(url) {
                    case .pair(let code):
                        if model.pair(with: code) { Haptics.attention() }
                    case .open:
                        model.handleUserOpened()
                    case .unrecognised:
                        // Still offered to the pairing parser: a code from somewhere else
                        // has no other way to report that it wasn't ours, and the pairing
                        // screen's message is the only place that can say so.
                        model.pair(with: url.absoluteString)
                    }
                }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background: model.didEnterBackground()
            case .active: model.didBecomeActive()
            default: break
            }
        }
    }
}
