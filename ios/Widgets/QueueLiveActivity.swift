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
            ActivityView(status: context.state, stale: context.isStale)
                .activityBackgroundTint(Color.black.opacity(0.85))
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let status = context.state
            let stale = context.isStale
            let tint = accent(status, stale: stale)
            return DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    ModeBadge(status: status, size: 44, tint: tint).padding(.leading, 4)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    ElapsedText(status: status, stale: stale)
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
                            .foregroundStyle(tint)
                        Text(subtitle(status, stale: stale))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } compactLeading: {
                Image(systemName: status.symbol)
                    .foregroundStyle(tint)
            } compactTrailing: {
                ElapsedText(status: status, stale: stale)
                    .monospacedDigit()
                    .frame(maxWidth: 52)
                    .foregroundStyle(tint)
            } minimal: {
                Image(systemName: status.symbol)
                    .foregroundStyle(tint)
            }
            .keylineTint(tint)
        }
        .supplementalActivityFamilies([.small])
    }
}

/// A stale activity hasn't heard from the worker in a while. It keeps its shape but loses the
/// colour and says why, rather than passing off a frozen timer as live.
private func accent(_ status: QueueStatus, stale: Bool) -> Color {
    stale ? QueueStatus.idle.accent : status.accent
}

private func subtitle(_ status: QueueStatus, stale: Bool) -> String {
    stale ? "Lost contact with your PC" : status.subtitle
}

private struct ActivityView: View {
    @Environment(\.activityFamily) private var family
    let status: QueueStatus
    let stale: Bool

    var body: some View {
        if family == .small {
            HStack(spacing: 8) {
                ModeBadge(status: status, size: 30, tint: accent(status, stale: stale))
                VStack(alignment: .leading, spacing: 0) {
                    Text(status.title)
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(accent(status, stale: stale))
                        .lineLimit(1)
                    if status.state == .idle || stale {
                        Text(subtitle(status, stale: stale))
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    } else {
                        ElapsedText(status: status)
                            .font(.system(size: 22, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(8)
        } else {
            HStack(spacing: 14) {
                ModeBadge(status: status, size: 52, tint: accent(status, stale: stale))
                VStack(alignment: .leading, spacing: 2) {
                    Text(status.title)
                        .font(.system(size: 20, weight: .bold, design: .rounded))
                        .foregroundStyle(accent(status, stale: stale))
                    Text(subtitle(status, stale: stale))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if status.state != .idle {
                    VStack(alignment: .trailing, spacing: 0) {
                        ElapsedText(status: status, stale: stale)
                            .font(.system(size: 34, weight: .semibold, design: .rounded))
                            .monospacedDigit()
                            .multilineTextAlignment(.trailing)
                            .frame(maxWidth: 130, alignment: .trailing)
                        Text(status.timerCaption)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(16)
        }
    }
}
