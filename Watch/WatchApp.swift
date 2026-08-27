import SwiftUI

@main
struct OverwatchQueueWatchApp: App {
    @State private var model = WatchModel()

    var body: some Scene {
        WindowGroup {
            WatchRootView()
                .environment(model)
                .environment(model.store)
                .task { model.start() }
        }
    }
}
