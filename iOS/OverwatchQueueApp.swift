import SwiftUI

@main
struct OverwatchQueueApp: App {
    @State private var model = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .environment(model.store)
                .environment(model.catalog)
                .preferredColorScheme(.dark)
                .task { model.start() }
                // Scanning the code in the system camera opens it as a URL. Handling it
                // here means the camera app is a way in, not just the in-app scanner.
                .onOpenURL { url in
                    if model.pair(with: url.absoluteString) { Haptics.attention() }
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
