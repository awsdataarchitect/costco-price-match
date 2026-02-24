import SwiftUI

struct MainTabView: View {
    var body: some View {
        TabView {
            ReceiptsView()
                .tabItem { Label("Receipts", systemImage: "doc.text") }
            DealsView()
                .tabItem { Label("Deals", systemImage: "tag") }
            AnalysisView()
                .tabItem { Label("Analyze", systemImage: "chart.bar") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gear") }
        }
        .tint(.orange)
    }
}

/// Reusable prompt shown in tabs when backend is not connected
struct ConnectPrompt: View {
    var body: some View {
        ContentUnavailableView {
            Label("Connect to AWS", systemImage: "server.rack")
        } description: {
            Text("Deploy the CDK stack to your AWS account, then connect in Settings.")
        } actions: {
            Text("Settings → Connect to AWS").font(.caption).foregroundStyle(.orange)
        }
    }
}
