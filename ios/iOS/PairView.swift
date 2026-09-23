import AVFoundation
import SwiftUI
import VisionKit

struct PairView: View {
    let resetNotice: Bool
    let onCode: (String) -> Void
    /// Offered on the first screen, not in the sheet you reach from a queue that's already paired.
    var onTimeItHere: (() -> Void)?

    @State private var cameraAllowed = AVCaptureDevice.authorizationStatus(for: .video) != .denied

    var body: some View {
        VStack(spacing: 20) {
            VStack(spacing: 8) {
                Text("Pair with your PC")
                    .font(.system(size: 30, weight: .bold, design: .rounded))
                Text(resetNotice
                     ? "The pairing code on your PC was reset. Scan the new one."
                     : "Open \(appName) on your PC and scan the QR code.")
                    .font(.body)
                    .foregroundStyle(resetNotice ? .orange : .secondary)
                    .multilineTextAlignment(.center)
            }
            .padding(.top, 32)

            Group {
                if DataScannerViewController.isSupported && cameraAllowed {
                    QRScanner(onCode: onCode)
                } else {
                    VStack(spacing: 14) {
                        Image(systemName: "camera.fill").font(.largeTitle)
                        Text("Camera unavailable. Allow camera access in Settings, or scan the code with the Camera app.")
                            .multilineTextAlignment(.center)
                            .foregroundStyle(.secondary)
                        if !cameraAllowed, let url = URL(string: UIApplication.openSettingsURLString) {
                            Button("Open Settings") { UIApplication.shared.open(url) }
                                .buttonStyle(.borderedProminent)
                        }
                    }
                    .padding()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.white.opacity(0.06))
                }
            }
            .frame(maxWidth: .infinity)
            .aspectRatio(1, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
            .padding(.horizontal, 24)

            if let onTimeItHere {
                Button(action: onTimeItHere) {
                    Label("No PC? Time a queue here", systemImage: "stopwatch")
                }
                .buttonStyle(.bordered)
            }

            Spacer()

            Text("Not affiliated with Overwatch or Blizzard Entertainment.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
                .padding(.bottom, 8)
        }
        .task {
            if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
                cameraAllowed = await AVCaptureDevice.requestAccess(for: .video)
            }
        }
    }
}

private struct QRScanner: UIViewControllerRepresentable {
    let onCode: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                                qualityLevel: .balanced,
                                                isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        try? scanner.startScanning()
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        if !scanner.isScanning { try? scanner.startScanning() }
    }

    static func dismantleUIViewController(_ scanner: DataScannerViewController, coordinator: Coordinator) {
        scanner.stopScanning()
    }

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onCode: (String) -> Void
        private var done = false

        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for item in items {
                guard !done, case .barcode(let code) = item, let value = code.payloadStringValue,
                      Pairing.parse(value) != nil else { continue }
                done = true
                UINotificationFeedbackGenerator().notificationOccurred(.success)
                onCode(value)
            }
        }
    }
}
