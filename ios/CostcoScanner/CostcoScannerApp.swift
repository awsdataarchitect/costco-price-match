import SwiftUI

@main
struct CostcoScannerApp: App {
    @StateObject private var config = BackendConfig.shared

    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environmentObject(config)
                .task {
                    // Auto-refresh token on launch if connected
                    if config.connected { await config.refreshToken() }
                }
        }
    }
}
