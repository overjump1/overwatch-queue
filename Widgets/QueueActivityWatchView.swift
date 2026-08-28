import SwiftUI
import WidgetKit

/// The Live Activity as it appears in the Apple Watch Smart Stack.
///
/// The watch mirrors the phone's activity by itself — this is only about what gets drawn
/// once it does. Three things differ enough from the Lock Screen to be worth a separate
/// view rather than a squeezed one:
///
/// - It is glanced at, not read. One line of status, one big number, nothing else.
/// - It is competing with the watch face, so the timer carries the emphasis and the
///   phase's colour does the rest of the work.
/// - The wait meter is dropped. At this width a progress bar is a few pixels of nothing,
///   and the elapsed time already says everything it would have.
///
/// Tapping it opens the watch app, which lands on the screen for the current phase
/// unaided — `WatchRootView` is a switch over exactly this state.
struct WatchActivityView: View {
    var state: QueueActivityAttributes.ContentState
    var attributes: QueueActivityAttributes

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: state.symbolName)
                .font(.title3.weight(.semibold))
                .foregroundStyle(Palette.accent(for: state.phase))
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 1) {
                Text(state.headline)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Palette.white.opacity(0.7))
                    .lineLimit(1)

                ActivityTimer(state: state, attributes: attributes)
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(timerTint)
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 4)
        .containerBackground(Palette.night.gradient, for: .widget)
    }

    /// A deadline is the one thing on this card the player is racing, so it takes the
    /// alarm colour; an elapsed count is just information and stays neutral.
    private var timerTint: Color {
        state.phase.deadline != nil ? Palette.orange : Palette.white
    }
}
