import AVFoundation
import SwiftUI

/// The whole of setup: point the camera at the code in the server window.
///
/// This is what an unpaired app shows instead of a queue, and there is deliberately
/// nothing else on it. An address and a token typed by hand were a second way in that
/// had to be kept working and could be got subtly wrong; the code carries both, and
/// scanning it cannot be mistyped.
struct PairingView: View {
    /// Set when this is a sheet over an already-paired app, so it can close itself once
    /// a new code lands. Nil when it *is* the screen, with nothing behind it to go back to.
    var onPaired: (() -> Void)?

    @Environment(AppModel.self) private var model

    @State private var scanner = ScannerModel()
    @State private var problem: String?

    var body: some View {
        ZStack {
            MeshBackground(phase: .idle, intensity: 0.7)

            VStack(spacing: 0) {
                header
                viewfinder
                    .frame(maxWidth: .infinity)
                    .aspectRatio(1, contentMode: .fit)
                    .padding(.horizontal, 36)
                    .padding(.top, 28)

                message
                    .frame(height: 60)
                    .padding(.horizontal, 32)

                Spacer(minLength: 0)
            }
        }
        .overlay(alignment: .topTrailing) { closeButton }
        .task { await scanner.start() }
        .onDisappear { scanner.stop() }
        .onAppear { model.clearPairingProblem() }
        .onChange(of: scanner.scannedCode) { _, code in
            guard let code else { return }
            accept(code)
        }
    }

    // MARK: - Pieces

    private var header: some View {
        VStack(spacing: 10) {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 40, weight: .light))
                .foregroundStyle(Palette.orange)

            Text("Pair with your PC")
                .font(.title2.weight(.semibold))
                .foregroundStyle(Palette.white)

            Text("Run the queue server on your PC and point this at the code it shows.")
                .font(.subheadline)
                .foregroundStyle(Palette.white.opacity(0.55))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 44)
        }
        .padding(.top, 56)
    }

    @ViewBuilder
    private var viewfinder: some View {
        ZStack {
            // Flexible in both directions, so the square below is the frame's doing
            // rather than whatever the message inside happens to measure.
            Color.clear

            switch scanner.state {
            case .running:
                CameraPreview(session: scanner.session)
            case .denied:
                unavailable("Camera access is off",
                            "Scanning the code is the only way to pair, so the app needs "
                            + "the camera.", "camera.badge.ellipsis") {
                    Button("Open Settings") {
                        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                        UIApplication.shared.open(url)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Palette.orange)
                    .padding(.top, 4)
                }
            case .unavailable:
                unavailable("No camera on this device",
                            "Pairing reads the code from the server window, so this needs "
                            + "a device with a camera.", "iphone.slash") { EmptyView() }
            case .starting:
                ProgressView().tint(Palette.white)
            }
        }
        .background(Palette.night.opacity(0.7))
        .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 28, style: .continuous)
                .strokeBorder(Palette.white.opacity(0.14), lineWidth: 1)
        }
    }

    private func unavailable<Action: View>(_ title: String, _ detail: String, _ symbol: String,
                                           @ViewBuilder action: () -> Action) -> some View {
        VStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(Palette.neutral)
            Text(title)
                .font(.headline)
                .foregroundStyle(Palette.white.opacity(0.9))
            Text(detail)
                .font(.footnote)
                .foregroundStyle(Palette.white.opacity(0.5))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 28)
            action()
        }
    }

    @ViewBuilder
    private var message: some View {
        if let problem = problem ?? model.pairingProblem {
            Label(problem, systemImage: "exclamationmark.triangle.fill")
                .font(.footnote.weight(.medium))
                .foregroundStyle(Palette.damage)
                .multilineTextAlignment(.center)
                .padding(.top, 16)
                .transition(.opacity)
        }
    }

    /// A sheet needs its own way out; the root version has nothing to go back to.
    @ViewBuilder
    private var closeButton: some View {
        if let onPaired {
            Button {
                onPaired()
            } label: {
                Image(systemName: "xmark")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Palette.white.opacity(0.8))
                    .padding(10)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .padding(.top, 12)
            .padding(.trailing, 20)
        }
    }

    // MARK: - Pairing

    private func accept(_ code: String) {
        guard model.pair(with: code) else {
            scanner.rearm()
            withAnimation { problem = "That isn't a pairing code from the queue server." }
            Haptics.warning()
            return
        }
        Haptics.attention()
        scanner.stop()
        onPaired?()
    }
}

// MARK: - Camera

/// Owns the capture session and hands back the first QR payload it sees.
@MainActor
@Observable
final class ScannerModel: NSObject, AVCaptureMetadataOutputObjectsDelegate {
    enum State { case starting, running, denied, unavailable }

    private(set) var state: State = .starting
    private(set) var scannedCode: String?

    let session = AVCaptureSession()
    private var isConfigured = false

    func start() async {
        guard await hasPermission() else {
            state = .denied
            return
        }
        guard configure() else {
            state = .unavailable
            return
        }
        state = .running
        // Starting the session blocks for a moment, which would stutter the transition in.
        let session = session
        await Task.detached(priority: .userInitiated) {
            if !session.isRunning { session.startRunning() }
        }.value
    }

    func stop() {
        let session = session
        Task.detached(priority: .utility) {
            if session.isRunning { session.stopRunning() }
        }
    }

    /// After a code that turned out not to be ours, so the next one is still read.
    func rearm() {
        scannedCode = nil
    }

    private func hasPermission() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video)
        default: return false
        }
    }

    private func configure() -> Bool {
        if isConfigured { return true }
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera,
                                                   for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera),
              session.canAddInput(input) else { return false }

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return false }

        session.beginConfiguration()
        session.addInput(input)
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
        session.commitConfiguration()

        isConfigured = true
        return true
    }

    nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput,
                                    didOutput objects: [AVMetadataObject],
                                    from connection: AVCaptureConnection) {
        guard let code = objects.compactMap({ $0 as? AVMetadataMachineReadableCodeObject }).first,
              let value = code.stringValue else { return }
        MainActor.assumeIsolated {
            guard scannedCode == nil else { return }      // one scan, not sixty a second
            scannedCode = value
        }
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.layer.session = session
        view.layer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ view: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        override var layer: AVCaptureVideoPreviewLayer {
            super.layer as! AVCaptureVideoPreviewLayer
        }
    }
}
