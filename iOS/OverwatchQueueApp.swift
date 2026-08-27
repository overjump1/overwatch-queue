import SwiftUI

@main
struct OverwatchQueueApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .environment(model.store)
                .environment(model.catalog)
                .preferredColorScheme(.dark)
                .task { model.start() }
        }
    }
}
