import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showSettings = false
    @State private var showScanner = false
    @State private var flash = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if model.pairID == nil {
                PairView(resetNotice: model.pairingWasReset) { model.pair(with: $0) }
            } else {
                queue
            }
        }
        .tint(.white)
        .onAppear { UIApplication.shared.isIdleTimerDisabled = true }
        .sheet(isPresented: $showScanner) {
            PairView(resetNotice: false) { code in
                model.pair(with: code)
                showScanner = false
            }
            .preferredColorScheme(.dark)
        }
    }

    private var queue: some View {
        let status = model.status
        return ZStack {
            RadialGradient(colors: [status.accent.opacity(status.state == .idle ? 0.08 : 0.28), .clear],
                           center: .center, startRadius: 10, endRadius: 420)
                .ignoresSafeArea()
                .animation(.easeInOut(duration: 0.6), value: status)

            Color.green.opacity(flash ? 0.45 : 0).ignoresSafeArea().allowsHitTesting(false)

            VStack(spacing: 18) {
                HStack {
                    Spacer()
                    Menu {
                        Button("Scan new code", systemImage: "qrcode.viewfinder") { showScanner = true }
                        Button("Unpair", systemImage: "xmark.circle", role: .destructive) { model.unpair() }
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .font(.title3)
                            .foregroundStyle(.secondary)
                            .padding(12)
                    }
                }
                Spacer()
                ModeBadge(status: status, size: 132)
                    .scaleEffect(flash ? 1.12 : 1)
                VStack(spacing: 6) {
                    Text(status.title)
                        .font(.system(size: 34, weight: .bold, design: .rounded))
                        .foregroundStyle(status.state == .idle ? .white : status.accent)
                    Text(status.state == .idle ? "Start a queue on your PC" : status.mode?.name ?? "Overwatch")
                        .font(.title3.weight(.medium))
                        .foregroundStyle(.secondary)
                }
                if status.state != .idle {
                    ElapsedText(status: status)
                        .font(.system(size: 76, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(status.state == .queueing ? .white : .secondary)
                }
                if status.state == .found {
                    Text("Head back to your PC")
                        .font(.headline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if !model.reachable {
                    Label("Can't reach the server — retrying", systemImage: "wifi.exclamationmark")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                }
            }
            .padding()
        }
        .onChange(of: model.matchAlerts) { _, _ in
            withAnimation(.easeOut(duration: 0.15)) { flash = true }
            withAnimation(.easeIn(duration: 0.9).delay(0.25)) { flash = false }
        }
    }
}
