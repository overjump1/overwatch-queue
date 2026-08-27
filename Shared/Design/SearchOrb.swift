import SwiftUI

/// The waiting animation: three counter-rotating arcs around a breathing core, with a
/// field of particles orbiting the whole thing.
///
/// The particles are drawn in a single `Canvas` rather than as dozens of views —
/// this runs for the entire length of a queue, sometimes many minutes, so it has to be
/// cheap enough not to be felt in the battery.
public struct SearchOrb: View {
    public var tint: Color
    public var symbolName: String
    /// 0…1 against the wait estimate. Drives the progress arc.
    public var progress: Double?
    /// Past the estimate the orb changes character rather than sitting at a full ring.
    public var isOverdue: Bool
    /// Particles are dropped on watchOS, where the frame budget is tighter and the orb is
    /// small enough that they'd read as noise.
    public var showsParticles: Bool

    public init(tint: Color, symbolName: String, progress: Double? = nil,
                isOverdue: Bool = false, showsParticles: Bool = true) {
        self.tint = tint
        self.symbolName = symbolName
        self.progress = progress
        self.isOverdue = isOverdue
        self.showsParticles = showsParticles
    }

    public var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 40.0, paused: false)) { context in
            let t = context.date.timeIntervalSinceReferenceDate

            ZStack {
                if showsParticles {
                    particleField(t)
                }
                arcs(t)
                progressArc
                core(t)
            }
        }
        // No .drawingGroup() here: rasterising the orb into its own layer leaves a
        // visible rectangular seam where it composites over the mesh background, and the
        // arcs and Canvas are cheap enough not to need it.
    }

    // MARK: - Layers

    private func arcs(_ t: TimeInterval) -> some View {
        ZStack {
            arc(trim: 0.62, width: 2, inset: 0, speed: 22, t: t, opacity: 0.35)
            arc(trim: 0.28, width: 3, inset: 14, speed: -34, t: t, opacity: 0.75)
            arc(trim: 0.14, width: 2, inset: 28, speed: 58, t: t, opacity: 0.5)
        }
    }

    private func arc(trim: Double, width: CGFloat, inset: CGFloat,
                     speed: Double, t: TimeInterval, opacity: Double) -> some View {
        Circle()
            .trim(from: 0, to: trim)
            .stroke(
                AngularGradient(colors: [tint.opacity(0), tint, tint.opacity(0)],
                                center: .center),
                style: StrokeStyle(lineWidth: width, lineCap: .round))
            .padding(inset)
            .rotationEffect(.degrees(t * speed))
            .opacity(opacity)
    }

    /// The one ring that means something: how far through the estimate we are.
    @ViewBuilder
    private var progressArc: some View {
        if let progress {
            Circle()
                .trim(from: 0, to: max(0.004, progress))
                .stroke(
                    isOverdue
                        ? AnyShapeStyle(AngularGradient(colors: [Palette.orange, Palette.amber, Palette.orange],
                                                        center: .center))
                        : AnyShapeStyle(tint),
                    style: StrokeStyle(lineWidth: 5, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .shadow(color: tint.opacity(0.6), radius: 8)
                .animation(.smooth(duration: 0.6), value: progress)
        }
    }

    private func core(_ t: TimeInterval) -> some View {
        let breathe = 1 + sin(t * 1.6) * 0.045

        return ZStack {
            Circle()
                .fill(RadialGradient(colors: [tint.opacity(0.45), tint.opacity(0.02)],
                                     center: .center, startRadius: 0, endRadius: 90))
                .padding(30)
                .blur(radius: 10)

            Image(systemName: symbolName)
                .font(.system(size: 44, weight: .semibold))
                .foregroundStyle(Palette.white)
                .shadow(color: tint.opacity(0.9), radius: 14)
                .symbolEffect(.pulse, options: .repeating)
        }
        .scaleEffect(breathe)
    }

    /// Particles orbit on fixed radii at varying speeds, so the field reads as depth
    /// rather than randomness.
    private func particleField(_ t: TimeInterval) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let base = min(size.width, size.height) / 2

            for i in 0..<28 {
                let seed = Double(i)
                let radius = base * (0.55 + (seed.truncatingRemainder(dividingBy: 7) / 7) * 0.55)
                let speed = 0.25 + (seed.truncatingRemainder(dividingBy: 5)) * 0.11
                let angle = t * speed + seed * 0.7
                let point = CGPoint(x: center.x + cos(angle) * radius,
                                    y: center.y + sin(angle) * radius)
                let dot = 1.2 + (seed.truncatingRemainder(dividingBy: 3))
                // Fade with orbit position so particles appear to pass behind the core.
                let alpha = 0.15 + (sin(angle * 1.5) + 1) / 2 * 0.5

                context.fill(
                    Path(ellipseIn: CGRect(x: point.x - dot / 2, y: point.y - dot / 2,
                                           width: dot, height: dot)),
                    with: .color(tint.opacity(alpha)))
            }
        }
    }
}
