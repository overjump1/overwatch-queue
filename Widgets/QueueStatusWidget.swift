import SwiftUI
import WidgetKit

/// Home Screen, Lock Screen and StandBy widget. Covers the gap the Live Activity leaves:
/// when nothing is queued there's no activity running, but the app should still be one
/// glance away.
struct QueueStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "QueueStatusWidget", provider: QueueStatus.Provider()) { entry in
            QueueStatusWidgetView(snapshot: entry.snapshot)
                .containerBackground(Palette.night.gradient, for: .widget)
        }
        .configurationDisplayName("Queue Status")
        .description("Your current Overwatch queue at a glance.")
        .supportedFamilies([.systemSmall, .accessoryRectangular, .accessoryCircular])
    }
}
