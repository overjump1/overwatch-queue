import SwiftUI

/// The one-shot celebration that fires the instant a match lands. Lives above every
/// phase view so it plays over whatever is on screen, and is driven by a token rather
/// than by the phase itself — that way it fires exactly once per match, never on a
/// payload refresh.
struct MatchFoundBurst: View {
    var token: Int

    var body: some View {
        KeyframeAnimator(initialValue: BurstFrame(), trigger: token) { frame in
            ZStack {
                // Screen flash — brief and bright, the part you catch out of the corner
                // of your eye when the phone is face-up on the desk.
                Rectangle()
                    .fill(Palette.white)
                    .opacity(frame.flash)
                    .ignoresSafeArea()

                // Two shockwaves, the second trailing the first, so the burst has a
                // sense of force rather than being one clean circle.
                shockwave(scale: frame.ring1, opacity: frame.ringOpacity1, width: 6)
                shockwave(scale: frame.ring2, opacity: frame.ringOpacity2, width: 3)
            }
        } keyframes: { _ in
            KeyframeTrack(\.flash) {
                LinearKeyframe(0.0, duration: 0.0)
                LinearKeyframe(0.55, duration: 0.07)
                CubicKeyframe(0.0, duration: 0.45)
            }
            KeyframeTrack(\.ring1) {
                LinearKeyframe(0.15, duration: 0.0)
                SpringKeyframe(3.2, duration: 0.85, spring: .snappy)
            }
            KeyframeTrack(\.ringOpacity1) {
                LinearKeyframe(0.0, duration: 0.0)
                LinearKeyframe(0.9, duration: 0.06)
                CubicKeyframe(0.0, duration: 0.79)
            }
            KeyframeTrack(\.ring2) {
                LinearKeyframe(0.1, duration: 0.12)
                SpringKeyframe(2.4, duration: 0.8, spring: .snappy)
            }
            KeyframeTrack(\.ringOpacity2) {
                LinearKeyframe(0.0, duration: 0.12)
                LinearKeyframe(0.7, duration: 0.06)
                CubicKeyframe(0.0, duration: 0.74)
            }
        }
    }

    private func shockwave(scale: Double, opacity: Double, width: CGFloat) -> some View {
        Circle()
            .stroke(
                RadialGradient(colors: [Palette.amber, Palette.orange],
                               center: .center, startRadius: 0, endRadius: 200),
                lineWidth: width)
            .frame(width: 240, height: 240)
            .scaleEffect(scale)
            .opacity(opacity)
            .blur(radius: 0.5)
    }

    struct BurstFrame {
        var flash: Double = 0
        var ring1: Double = 0.15
        var ring2: Double = 0.1
        var ringOpacity1: Double = 0
        var ringOpacity2: Double = 0
    }
}

/// The match-found screen itself: how long you waited, and a hard deadline to act on.
struct MatchFoundView: View {
    var info: MatchFoundInfo
    @Environment(QueueStore.self) private var store
    @State private var appeared = false

    private var deadline: Date { store.clock.toLocal(info.lockInAt) }

    var body: some View {
        VStack(spacing: 26) {
            Spacer()

            ZStack {
                CountdownRing(deadline: deadline, total: 15, lineWidth: 6)
                    .frame(width: 210, height: 210)

                VStack(spacing: 4) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 40, weight: .bold))
                        .foregroundStyle(Palette.orange)
                        .symbolEffect(.bounce, options: .repeating.speed(0.6))

                    Text(timerInterval: Date.now...deadline, countsDown: true)
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(Palette.white)

                    Text("until you're in")
                        .font(.caption2)
                        .foregroundStyle(Palette.white.opacity(0.5))
                }
            }
            .scaleEffect(appeared ? 1 : 0.4)
            .opacity(appeared ? 1 : 0)

            VStack(spacing: 8) {
                Text("MATCH FOUND")
                    .font(.system(size: 34, weight: .black, design: .rounded))
                    .tracking(1.5)
                    .foregroundStyle(
                        LinearGradient(colors: [Palette.white, Palette.amber],
                                       startPoint: .top, endPoint: .bottom))

                Text("\(info.mode.displayName) · waited \(QueueTime.compact(info.waited))")
                    .font(.subheadline)
                    .foregroundStyle(Palette.white.opacity(0.65))
            }
            .scaleEffect(appeared ? 1 : 0.8)
            .opacity(appeared ? 1 : 0)

            Spacer()

            // No Accept button: Overwatch 2 has no accept prompt, it just puts you in
            // the game. The only real action is to try to bail out, and that often
            // doesn't land — so it's offered quietly and honestly rather than as a
            // primary action that implies control the player doesn't have.
            VStack(spacing: 8) {
                Button(role: .destructive) {
                    store.cancelQueue()
                } label: {
                    Text("Try to Cancel")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .tint(Palette.neutral)
                .controlSize(.large)

                Text("Only works if you're quick — and not always then.")
                    .font(.caption2)
                    .foregroundStyle(Palette.white.opacity(0.45))
            }
            .padding(.horizontal, 32)
            .padding(.bottom, 40)
            .opacity(appeared ? 1 : 0)
        }
        .onAppear {
            withAnimation(.bouncy(duration: 0.7, extraBounce: 0.25)) { appeared = true }
        }
    }
}
