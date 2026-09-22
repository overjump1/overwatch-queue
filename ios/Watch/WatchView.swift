import SwiftUI

struct WatchView: View {
    @EnvironmentObject private var model: WatchModel

    var body: some View {
        if model.pairID == nil {
            VStack(spacing: 8) {
                Image(systemName: "iphone.gen3")
                    .font(.title2)
                    .foregroundStyle(.secondary)
                Text("Pair on your iPhone")
                    .font(.headline)
                Text("Open OverQueue on your iPhone and scan the code on your PC.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
        } else {
            let status = model.status
            VStack(spacing: 6) {
                ModeBadge(status: status, size: 54)
                Text(status.title)
                    .font(.system(size: 18, weight: .bold, design: .rounded))
                    .foregroundStyle(status.state == .idle ? .white : status.accent)
                if status.state != .idle {
                    Text(status.subtitle)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .minimumScaleFactor(0.8)
                        .lineLimit(1)
                    ElapsedText(status: status)
                        .font(.system(size: 34, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(status.state == .found ? .secondary : .primary)
                }
                if !model.reachable {
                    Image(systemName: "wifi.exclamationmark")
                        .foregroundStyle(.orange)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(
                RadialGradient(colors: [status.accent.opacity(status.state == .idle ? 0.1 : 0.35), .black],
                               center: .center, startRadius: 5, endRadius: 140)
                    .ignoresSafeArea()
            )
        }
    }
}
