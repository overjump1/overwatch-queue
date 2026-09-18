import SwiftUI

#if os(iOS)
import ActivityKit

struct QueueActivityAttributes: ActivityAttributes {
    typealias ContentState = QueueStatus
}
#endif

struct ModeBadge: View {
    let status: QueueStatus
    var size: CGFloat = 44

    var body: some View {
        ZStack {
            Circle().fill(status.accent.opacity(status.state == .idle ? 0.18 : 0.25))
            Circle().strokeBorder(status.accent, lineWidth: max(1.5, size / 28))
            Image(systemName: status.symbol)
                .font(.system(size: size * 0.42, weight: .bold))
                .foregroundStyle(status.accent)
        }
        .frame(width: size, height: size)
    }
}

extension QueueStatus {
    var symbol: String {
        switch state {
        case .found: "checkmark"
        case .queueing: mode?.symbol ?? "hourglass"
        case .playing: "flag.checkered"
        case .idle: "moon.zzz.fill"
        }
    }

    /// The word under the timer.
    var timerCaption: String {
        switch state {
        case .queueing: "in queue"
        case .found: "waited"
        case .playing: "in match"
        case .idle: ""
        }
    }
}

/// Counts up while queueing; shows the final wait once a match is found; counts the match once it's on.
struct ElapsedText: View {
    let status: QueueStatus

    var body: some View {
        switch status.state {
        case .queueing:
            if let start = status.startDate {
                Text(timerInterval: start...Date.distantFuture, countsDown: false)
            } else {
                Text("0:00")
            }
        case .found:
            Text(clockString(status.waited ?? 0))
        case .playing:
            if let found = status.foundDate {
                Text(timerInterval: found...Date.distantFuture, countsDown: false)
            } else {
                Text("–:––")
            }
        case .idle:
            Text("–:––")
        }
    }
}
