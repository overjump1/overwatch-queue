import ActivityKit
import SwiftUI
import WidgetKit

@main
struct QueueWidgets: WidgetBundle {
    var body: some Widget {
        QueueLiveActivity()
    }
}

struct QueueLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: QueueActivityAttributes.self) { context in
            ActivityView(status: context.state)
                .activityBackgroundTint(Color.black.opacity(0.85))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let status = context.state
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    ModeBadge(status: status, size: 44).padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    ElapsedText(status: status)
                        .font(.system(size: 28, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: 110, alignment: .trailing)
                        .padding(.trailing, 4)
                }
                DynamicIslandExpandedRegion(.center) {
                    VStack(spacing: 2) {
                        Text(status.title)
                            .font(.headline)
                            .foregroundStyle(status.accent)
                        Text(status.mode?.name ?? "Overwatch")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } compactLeading: {
                Image(systemName: status.state == .found ? "checkmark.circle.fill" : status.mode?.symbol ?? "hourglass")
                    .foregroundStyle(status.accent)
            } compactTrailing: {
                ElapsedText(status: status)
                    .monospacedDigit()
                    .frame(maxWidth: 52)
                    .foregroundStyle(status.accent)
            } minimal: {
                Image(systemName: status.state == .found ? "checkmark.circle.fill" : status.mode?.symbol ?? "hourglass")
                    .foregroundStyle(status.accent)
            }
            .keylineTint(status.accent)
        }
        .supplementalActivityFamilies([.small])
    }
}

private struct ActivityView: View {
    @Environment(\.activityFamily) private var family
    let status: QueueStatus

    var body: some View {
        if family == .small {
            HStack(spacing: 8) {
                ModeBadge(status: status, size: 30)
                VStack(alignment: .leading, spacing: 0) {
                    Text(status.title)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(status.accent)
                        .lineLimit(1)
                    ElapsedText(status: status)
                        .font(.system(size: 22, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                }
                Spacer(minLength: 0)
            }
            .padding(8)
        } else {
            HStack(spacing: 14) {
                ModeBadge(status: status, size: 52)
                VStack(alignment: .leading, spacing: 2) {
                    Text(status.title)
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .foregroundStyle(status.accent)
                    Text(status.mode?.name ?? "Overwatch")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 0) {
                    ElapsedText(status: status)
                        .font(.system(size: 34, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: 130, alignment: .trailing)
                    Text(status.state == .found ? "waited" : "in queue")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(16)
        }
    }
}
