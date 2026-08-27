import CoreHaptics
import UIKit

/// Match-found needs to be felt through a pocket, so it gets a bespoke pattern rather
/// than the stock success chime: a hard transient, a second one a beat later, then a
/// short rumble that decays. Everything degrades to `UINotificationFeedbackGenerator`
/// on hardware without a haptic engine.
@MainActor
public enum Haptics {
    private static var engine: CHHapticEngine?

    private static func makeEngine() -> CHHapticEngine? {
        guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else { return nil }
        if let engine { return engine }
        do {
            let engine = try CHHapticEngine()
            // The system stops the engine when the app backgrounds or on interruption;
            // restarting on demand is cheaper than holding it running for a whole queue.
            engine.stoppedHandler = { _ in Self.engine = nil }
            engine.resetHandler = { try? Self.engine?.start() }
            try engine.start()
            Self.engine = engine
            return engine
        } catch {
            return nil
        }
    }

    public static func matchFound() {
        guard let engine = makeEngine() else {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            return
        }

        let events: [CHHapticEvent] = [
            CHHapticEvent(eventType: .hapticTransient, parameters: [
                .init(parameterID: .hapticIntensity, value: 1.0),
                .init(parameterID: .hapticSharpness, value: 0.9),
            ], relativeTime: 0),

            CHHapticEvent(eventType: .hapticTransient, parameters: [
                .init(parameterID: .hapticIntensity, value: 1.0),
                .init(parameterID: .hapticSharpness, value: 0.7),
            ], relativeTime: 0.12),

            CHHapticEvent(eventType: .hapticContinuous, parameters: [
                .init(parameterID: .hapticIntensity, value: 0.85),
                .init(parameterID: .hapticSharpness, value: 0.25),
            ], relativeTime: 0.2, duration: 0.55),
        ]

        // Fade the rumble out instead of cutting it, which reads as a thud rather than a click.
        let decay = CHHapticParameterCurve(
            parameterID: .hapticIntensityControl,
            controlPoints: [
                .init(relativeTime: 0, value: 1.0),
                .init(relativeTime: 0.35, value: 0.5),
                .init(relativeTime: 0.55, value: 0.0),
            ],
            relativeTime: 0.2)

        do {
            let pattern = try CHHapticPattern(events: events, parameterCurves: [decay])
            try engine.makePlayer(with: pattern).start(atTime: CHHapticTimeImmediate)
        } catch {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    /// Softer cue for "you need to choose something now".
    public static func attention() {
        UIImpactFeedbackGenerator(style: .rigid).impactOccurred(intensity: 0.8)
    }

    public static func selection() {
        UISelectionFeedbackGenerator().selectionChanged()
    }
}
