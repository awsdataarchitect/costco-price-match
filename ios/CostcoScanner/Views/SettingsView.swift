import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var config: BackendConfig
    @State private var showTerms = false
    @State private var showPrivacy = false
    @State private var showDisconnect = false
    @State private var apiInput = ""
    @State private var connecting = false

    var body: some View {
        NavigationStack {
            List {
                // Connect / Status section
                if config.isConnected {
                    Section("Backend") {
                        LabeledContent("API", value: config.apiURL.replacingOccurrences(of: "https://", with: ""))
                            .lineLimit(1)
                        HStack {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                            Text("Connected").foregroundStyle(.green)
                        }
                    }
                    Section {
                        Button(role: .destructive) { showDisconnect = true } label: {
                            HStack { Spacer(); Label("Disconnect", systemImage: "xmark.circle"); Spacer() }
                        }
                    }
                } else {
                    Section {
                        VStack(spacing: 12) {
                            Image(systemName: "server.rack")
                                .font(.system(size: 36)).foregroundStyle(.orange)
                            Text("Connect to AWS")
                                .font(.headline)
                            Text("Deploy the CDK stack to your AWS account, then paste your API URL below. Credentials are fetched automatically.")
                                .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity).padding(.vertical, 8)
                    }
                    Section("API Endpoint") {
                        TextField("https://xyz.execute-api.us-west-2.amazonaws.com", text: $apiInput)
                            .textContentType(.URL).autocorrectionDisabled().textInputAutocapitalization(.never)
                            .font(.subheadline.monospaced())
                    }
                    Section {
                        Button {
                            connecting = true
                            Task {
                                await config.connect(apiURL: apiInput)
                                connecting = false
                            }
                        } label: {
                            HStack {
                                Spacer()
                                if connecting { ProgressView().padding(.trailing, 4) }
                                Text(connecting ? "Connecting..." : "Connect")
                                    .fontWeight(.semibold)
                                Spacer()
                            }
                        }
                        .disabled(apiInput.isEmpty || connecting)
                    }
                    if let err = config.error {
                        Section {
                            Text(err).font(.caption).foregroundStyle(.red)
                        }
                    }
                    Section("How to Deploy") {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("1. Clone the repo from GitHub")
                            Text("2. cd infra && npm install")
                            Text("3. NOTIFY_EMAIL=you@email.com ./deploy.sh")
                            Text("4. Paste the API URL output above")
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Legal") {
                    Button { showTerms = true } label: {
                        Label("Terms of Use", systemImage: "doc.text")
                    }
                    Button { showPrivacy = true } label: {
                        Label("Privacy Policy", systemImage: "hand.raised")
                    }
                }
                Section("About") {
                    LabeledContent("Version", value: "1.0.0")
                    LabeledContent("Developer", value: "Waltsoft Inc.")
                    Link(destination: URL(string: "https://waltsoft.net")!) {
                        Label("Website", systemImage: "globe")
                    }
                    Link(destination: URL(string: "https://github.com/awsdataarchitect/costco-price-match")!) {
                        Label("GitHub", systemImage: "link")
                    }
                }
            }
            .navigationTitle("Settings")
            .sheet(isPresented: $showTerms) { LegalSheet(title: "Terms of Use", content: Self.termsText) }
            .sheet(isPresented: $showPrivacy) { LegalSheet(title: "Privacy Policy", content: Self.privacyText) }
            .confirmationDialog("Disconnect from this backend?", isPresented: $showDisconnect, titleVisibility: .visible) {
                Button("Disconnect", role: .destructive) { config.disconnect() }
            } message: {
                Text("This will clear your backend configuration and credentials.")
            }
        }
    }

    // MARK: - Legal Text

    static let termsText = """
    Last Updated: February 17, 2026

    Welcome to CostScanner, developed by Waltsoft Inc. ("we", "us", "our"). By using this application, you agree to these Terms of Use.

    1. Acceptance of Terms
    By downloading, installing, or using CostScanner, you agree to be bound by these terms. If you do not agree, do not use the app.

    2. Description of Service
    CostScanner is a personal finance tool that helps you track Costco purchases and identify price adjustment opportunities. The app connects to your own self-hosted AWS backend infrastructure to scan receipt PDFs, cross-reference purchases against publicly available deal information, and provide analysis.

    3. Bring Your Own Infrastructure (BYOI)
    CostScanner operates on a BYOI model. You are responsible for deploying and maintaining your own AWS backend using the provided CDK templates. All data processing, storage, and AI analysis occurs within your own AWS account. Waltsoft Inc. does not host, operate, or have access to your backend infrastructure.

    4. Not Affiliated with Costco
    This app is not affiliated with, endorsed by, or sponsored by Costco Wholesale Corporation. "Costco" is a registered trademark of Costco Wholesale Corporation.

    5. Your Responsibilities
    You are responsible for your own AWS account, credentials, and infrastructure costs. You are responsible for securing your API endpoint and any data stored in your AWS account.

    6. Acceptable Use
    You agree to use the app only for personal, non-commercial purposes. You will not attempt to reverse engineer, modify, or distribute the app or its content.

    7. Data Accuracy
    While we strive for accuracy, we do not guarantee that price information, deal data, or receipt parsing results are error-free. Always verify price adjustments with Costco directly.

    8. Limitation of Liability
    Waltsoft Inc. shall not be liable for any indirect, incidental, or consequential damages arising from your use of the app, including AWS costs incurred by your infrastructure. The app is provided "as is" without warranties of any kind.

    9. Modifications
    We reserve the right to modify these terms at any time. Continued use of the app constitutes acceptance of updated terms.

    10. Contact
    For questions about these terms, visit https://waltsoft.net.
    """

    static let privacyText = """
    Last Updated: February 17, 2026

    Waltsoft Inc. ("we", "us", "our") develops CostScanner. This Privacy Policy explains how data is handled when you use the app.

    1. BYOI Architecture
    CostScanner uses a Bring Your Own Infrastructure (BYOI) model. You deploy your own AWS backend, and all data is stored and processed entirely within your own AWS account. Waltsoft Inc. does not operate any servers, databases, or cloud infrastructure on your behalf.

    2. What We Do NOT Collect
    We do not collect, store, transmit, or have access to:
    • Your receipt data or purchase history
    • Your AWS credentials or API endpoints
    • Your personal information or email address
    • Any analytics or usage data

    3. Data That Stays in Your AWS Account
    The following data is created and stored solely in your own AWS account:
    • Receipt PDFs (Amazon S3)
    • Parsed receipt data and deals (Amazon DynamoDB)
    • Authentication credentials (Amazon Cognito)
    • AI analysis results (processed via Amazon Bedrock)

    4. Local Device Storage
    The app stores your API endpoint URL and authentication token locally on your device using standard iOS storage. This data never leaves your device except to communicate with your own backend.

    5. Third-Party Services
    Your self-hosted backend uses AWS services within your own account. Waltsoft Inc. has no relationship with or access to your AWS account.

    6. Data Deletion
    Since all data resides in your own AWS account, you have full control. Delete individual items through the app, or destroy your entire CDK stack to remove all data.

    7. Children's Privacy
    This app is not intended for children under 13.

    8. Changes to This Policy
    We may update this policy from time to time. Changes will be reflected in the app.

    9. Contact
    For privacy concerns, visit https://waltsoft.net.
    """
}

// MARK: - Legal Sheet

struct LegalSheet: View {
    let title: String
    let content: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // Header card
                    HStack(spacing: 12) {
                        Image(systemName: title.contains("Terms") ? "doc.text.fill" : "hand.raised.fill")
                            .font(.title2).foregroundStyle(.orange)
                            .frame(width: 44, height: 44)
                            .background(.orange.opacity(0.12))
                            .cornerRadius(10)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(title).font(.title3.bold())
                            Text("Waltsoft Inc.").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                    .padding(16)
                    .background(
                        RoundedRectangle(cornerRadius: 16)
                            .fill(Color(.secondarySystemGroupedBackground))
                    )

                    // Content
                    ForEach(content.components(separatedBy: "\n\n"), id: \.self) { paragraph in
                        let trimmed = paragraph.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !trimmed.isEmpty {
                            if trimmed.first?.isNumber == true || trimmed.hasPrefix("Last Updated") {
                                if trimmed.hasPrefix("Last Updated") {
                                    Text(trimmed)
                                        .font(.caption).foregroundStyle(.secondary)
                                } else {
                                    // Section header like "1. Acceptance"
                                    let parts = trimmed.split(separator: "\n", maxSplits: 1)
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text(String(parts[0])).font(.subheadline.bold())
                                        if parts.count > 1 {
                                            Text(String(parts[1])).font(.subheadline).foregroundStyle(.secondary)
                                        }
                                    }
                                }
                            } else if trimmed.hasPrefix("•") {
                                VStack(alignment: .leading, spacing: 4) {
                                    ForEach(trimmed.components(separatedBy: "\n"), id: \.self) { line in
                                        Text(line).font(.subheadline).foregroundStyle(.secondary)
                                    }
                                }
                            } else {
                                Text(trimmed).font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .fontWeight(.semibold)
                }
            }
        }
    }
}
