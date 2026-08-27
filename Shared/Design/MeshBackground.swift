import SwiftUI

/// A slow-drifting mesh gradient behind everything. The colours are keyed to the phase,
/// so the screen's whole mood shifts as the queue progresses — cold and still while
/// waiting, hot and moving the instant a match lands — before the user reads a word.
public struct MeshBackground: View {
    public var phase: QueuePhase
    /// Multiplies drift speed. Match-found cranks this up.
    public var intensity: Double

    public init(phase: QueuePhase, intensity: Double = 1) {
        self.phase = phase
        self.intensity = intensity
    }

    public var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0, paused: false)) { context in
            let t = context.date.timeIntervalSinceReferenceDate * 0.18 * intensity
            MeshGradient(width: 3, height: 3,
                         points: points(t),
                         colors: colors,
                         smoothsColors: true)
            .ignoresSafeArea()
        }
        .background(Palette.night)
        .animation(.easeInOut(duration: 0.9), value: phase.kind)
    }

    private var colors: [Color] { Palette.mesh(for: phase) }

    /// Only the interior control points move; pinning the border keeps the gradient from
    /// tearing away from the screen edges as it animates.
    private func points(_ t: Double) -> [SIMD2<Float>] {
        func wobble(_ base: SIMD2<Float>, _ seed: Double, _ amount: Float = 0.16) -> SIMD2<Float> {
            SIMD2(base.x + Float(sin(t + seed)) * amount,
                  base.y + Float(cos(t * 0.8 + seed * 1.3)) * amount)
        }
        return [
            SIMD2(0, 0),                       SIMD2(0.5, 0),                     SIMD2(1, 0),
            wobble(SIMD2(0, 0.5), 1.0, 0.05),  wobble(SIMD2(0.5, 0.5), 2.0),      wobble(SIMD2(1, 0.5), 3.0, 0.05),
            SIMD2(0, 1),                       SIMD2(0.5, 1),                     SIMD2(1, 1),
        ]
    }
}
