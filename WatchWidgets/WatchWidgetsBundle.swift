import SwiftUI
import WidgetKit

@main
struct WatchQueueWidgetsBundle: WidgetBundle {
    var body: some Widget {
        WatchQueueWidget()
    }
}

/// Smart Stack accessory. Surfaces the queue on the wrist without opening the app —
/// which, for a feature whose whole point is "tell me while I'm away from the PC", is
/// where it earns its keep.
struct WatchQueueWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "WatchQueueWidget", provider: QueueStatus.Provider()) { entry in
            QueueStatusWidgetView(snapshot: entry.snapshot)
                .containerBackground(Palette.night.gradient, for: .widget)
        }
        .configurationDisplayName("Queue Status")
        .description("Your Overwatch queue in the Smart Stack.")
        .supportedFamilies([.accessoryRectangular, .accessoryCircular, .accessoryCorner])
    }
}
