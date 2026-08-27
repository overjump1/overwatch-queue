import ActivityKit
import SwiftUI
import WidgetKit

/// The Lock Screen banner and Dynamic Island presentations.
///
/// Every timer here is driven by `Text(timerInterval:)` against an absolute date carried
/// in the content state, so the whole thing keeps counting with no updates from the app.
struct QueueLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: QueueActivityAttributes.self) { context in
            LockScreenView(state: context.state, attributes: context.attributes)
                .activityBackgroundTint(Palette.night.opacity(0.92))
                .activitySystemActionForegroundColor(Palette.orange)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    Image(systemName: context.state.symbolName)
                        .font(.title2)
                        .foregroundStyle(accent(context.state.phase))
                        .padding(.leading, 4)
                }

                DynamicIslandExpandedRegion(.trailing) {
                    timerText(for: context.state, attributes: context.attributes)
                        .font(.system(.title2, design: .rounded, weight: .bold))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white)
                        .padding(.trailing, 4)
                }

                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.headline)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Palette.white.opacity(0.75))
                }

                DynamicIslandExpandedRegion(.bottom) {
                    ExpandedBottom(state: context.state)
                }
            } compactLeading: {
                Image(systemName: context.state.symbolName)
                    .foregroundStyle(accent(context.state.phase))
            } compactTrailing: {
                timerText(for: context.state, attributes: context.attributes)
                    .monospacedDigit()
                    .foregroundStyle(Palette.white)
                    .frame(maxWidth: 54)
            } minimal: {
                Image(systemName: context.state.symbolName)
                    .foregroundStyle(accent(context.state.phase))
            }
            .widgetURL(URL(string: "owqueue://open"))
            .keylineTint(accent(context.state.phase))
        }
    }

    private func accent(_ phase: QueuePhase) -> Color { Palette.accent(for: phase) }

    /// Counts up while searching, down against a deadline while the player owes an action.
    @ViewBuilder
    private func timerText(for state: QueueActivityAttributes.ContentState,
                           attributes: QueueActivityAttributes) -> some View {
        if let deadline = state.phase.deadline {
            Text(timerInterval: Date.now...deadline, countsDown: true)
        } else if case .searching(let info) = state.phase {
            Text(timerInterval: info.startedAt...Date.distantFuture, countsDown: false)
        } else if case .inGame(let info) = state.phase {
            Text(timerInterval: info.startedAt...Date.distantFuture, countsDown: false)
        } else {
            Text(attributes.startedAt, style: .timer)
        }
    }
}

private struct ExpandedBottom: View {
    var state: QueueActivityAttributes.ContentState

    var body: some View {
        switch state.phase {
        case .searching(let info):
            VStack(spacing: 6) {
                WaitMeter(progress: info.progress(),
                          isOverdue: info.isOverdue(),
                          tint: info.role.tint)
                HStack {
                    Text(info.mode.displayName)
                    Spacer()
                    Text(info.isOverdue() ? "Longer than usual"
                                          : QueueTime.estimate(info.estimatedWait))
                }
                .font(.caption2)
                .foregroundStyle(Palette.white.opacity(0.6))
            }
            .padding(.horizontal, 4)

        case .mapVote(let info):
            HStack(spacing: 6) {
                ForEach(info.options.prefix(3)) { option in
                    Text(option.mapKey.replacingOccurrences(of: "-", with: " ").capitalized)
                        .font(.caption2.weight(.medium))
                        .lineLimit(1)
                        .padding(.vertical, 4)
                        .frame(maxWidth: .infinity)
                        .background(info.myVote == option.mapKey
                                    ? Palette.orange.opacity(0.35)
                                    : Palette.white.opacity(0.10),
                                    in: Capsule())
                }
            }
            .foregroundStyle(Palette.white)

        default:
            Text(state.headline)
                .font(.caption2)
                .foregroundStyle(Palette.white.opacity(0.6))
        }
    }
}

private struct LockScreenView: View {
    var state: QueueActivityAttributes.ContentState
    var attributes: QueueActivityAttributes

    var body: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(Palette.accent(for: state.phase).opacity(0.18))
                Image(systemName: state.symbolName)
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Palette.accent(for: state.phase))
            }
            .frame(width: 46, height: 46)

            VStack(alignment: .leading, spacing: 3) {
                Text(state.headline)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Palette.white)

                if case .searching(let info) = state.phase {
                    WaitMeter(progress: info.progress(),
                              isOverdue: info.isOverdue(),
                              tint: info.role.tint)
                        .frame(maxWidth: 150)
                    Text(info.isOverdue() ? "Longer than usual"
                                          : QueueTime.estimate(info.estimatedWait))
                        .font(.caption2)
                        .foregroundStyle(Palette.white.opacity(0.55))
                }
            }

            Spacer(minLength: 4)

            VStack(alignment: .trailing, spacing: 2) {
                if let deadline = state.phase.deadline {
                    Text(timerInterval: Date.now...deadline, countsDown: true)
                        .font(.system(size: 26, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.orange)
                    Text("remaining").font(.caption2)
                        .foregroundStyle(Palette.white.opacity(0.45))
                } else if case .searching(let info) = state.phase {
                    Text(timerInterval: info.startedAt...Date.distantFuture, countsDown: false)
                        .font(.system(size: 26, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white)
                    Text("waiting").font(.caption2)
                        .foregroundStyle(Palette.white.opacity(0.45))
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }
}
