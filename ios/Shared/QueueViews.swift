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
            Image(systemName: symbol)
                .font(.system(size: size * 0.42, weight: .bold))
                .foregroundStyle(status.accent)
        }
        .frame(width: size, height: size)
    }

    private var symbol: String {
        switch status.state {
        case .found: "checkmark"
        case .queueing: status.mode?.symbol ?? "hourglass"
        case .idle: "moon.zzz.fill"
        }
    }
}

/// Counts up while queueing; shows the final wait once a match is found.
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
        case .idle:
            Text("–:––")
        }
    }
}
