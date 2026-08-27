import SwiftUI
import WidgetKit

@main
struct QueueWidgetsBundle: WidgetBundle {
    var body: some Widget {
        QueueStatusWidget()
        QueueLiveActivityWidget()
    }
}
