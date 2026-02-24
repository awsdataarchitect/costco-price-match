import Foundation

final class BackendConfig: ObservableObject {
    static let shared = BackendConfig()

    private let defaults = UserDefaults.standard
    private enum Key: String { case apiURL, poolID, clientID, region, username, password, idToken, connected }

    @Published var apiURL: String { didSet { defaults.set(apiURL, forKey: Key.apiURL.rawValue) } }
    @Published var poolID: String { didSet { defaults.set(poolID, forKey: Key.poolID.rawValue) } }
    @Published var clientID: String { didSet { defaults.set(clientID, forKey: Key.clientID.rawValue) } }
    @Published var region: String { didSet { defaults.set(region, forKey: Key.region.rawValue) } }
    @Published var username: String { didSet { defaults.set(username, forKey: Key.username.rawValue) } }
    @Published var password: String { didSet { defaults.set(password, forKey: Key.password.rawValue) } }
    @Published var idToken: String { didSet { defaults.set(idToken, forKey: Key.idToken.rawValue) } }
    @Published var connected: Bool { didSet { defaults.set(connected, forKey: Key.connected.rawValue) } }
    @Published var error: String?

    var hasEndpoint: Bool { !apiURL.isEmpty }
    var isConnected: Bool { connected && !idToken.isEmpty }

    init() {
        apiURL = defaults.string(forKey: Key.apiURL.rawValue) ?? ""
        poolID = defaults.string(forKey: Key.poolID.rawValue) ?? ""
        clientID = defaults.string(forKey: Key.clientID.rawValue) ?? ""
        region = defaults.string(forKey: Key.region.rawValue) ?? ""
        username = defaults.string(forKey: Key.username.rawValue) ?? ""
        password = defaults.string(forKey: Key.password.rawValue) ?? ""
        idToken = defaults.string(forKey: Key.idToken.rawValue) ?? ""
        connected = defaults.bool(forKey: Key.connected.rawValue)
    }

    /// Fetch config + credentials from backend, then sign in
    func connect(apiURL: String) async {
        let cleanURL = apiURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        await MainActor.run { self.error = nil }

        guard let configURL = URL(string: "\(cleanURL)/api/config") else {
            await MainActor.run { self.error = "Invalid API URL" }
            return
        }

        do {
            let (data, resp) = try await URLSession.shared.data(from: configURL)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                await MainActor.run { self.error = "Could not reach backend (HTTP \((resp as? HTTPURLResponse)?.statusCode ?? 0))" }
                return
            }
            let config = try JSONDecoder().decode(ConfigResponse.self, from: data)

            let token = try await cognitoSignIn(
                poolID: config.user_pool_id,
                clientID: config.user_pool_client_id,
                username: config.username,
                password: config.password,
                region: config.region
            )

            await MainActor.run {
                self.apiURL = cleanURL
                self.poolID = config.user_pool_id
                self.clientID = config.user_pool_client_id
                self.region = config.region
                self.username = config.username
                self.password = config.password
                self.idToken = token
                self.connected = true
            }
        } catch {
            await MainActor.run { self.error = error.localizedDescription }
        }
    }

    /// Refresh token using stored credentials
    func refreshToken() async {
        guard hasEndpoint && !username.isEmpty && !password.isEmpty && !poolID.isEmpty else { return }
        do {
            let token = try await cognitoSignIn(poolID: poolID, clientID: clientID, username: username, password: password, region: region)
            await MainActor.run { self.idToken = token }
        } catch {
            await MainActor.run { self.connected = false; self.idToken = "" }
        }
    }

    func disconnect() {
        apiURL = ""; poolID = ""; clientID = ""; region = ""
        username = ""; password = ""; idToken = ""; connected = false
        for key in [Key.apiURL, .poolID, .clientID, .region, .username, .password, .idToken, .connected] {
            defaults.removeObject(forKey: key.rawValue)
        }
    }

    // MARK: - Cognito USER_PASSWORD_AUTH

    private func cognitoSignIn(poolID: String, clientID: String, username: String, password: String, region: String) async throws -> String {
        let url = URL(string: "https://cognito-idp.\(region).amazonaws.com/")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/x-amz-json-1.1", forHTTPHeaderField: "Content-Type")
        req.setValue("AWSCognitoIdentityProviderService.InitiateAuth", forHTTPHeaderField: "X-Amz-Target")
        let body: [String: Any] = [
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": clientID,
            "AuthParameters": ["USERNAME": username, "PASSWORD": password]
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
            let errBody = String(data: data, encoding: .utf8) ?? "Unknown error"
            if errBody.contains("NEW_PASSWORD_REQUIRED") {
                throw AuthError.newPasswordRequired
            }
            throw AuthError.signInFailed(errBody)
        }
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let result = json?["AuthenticationResult"] as? [String: Any],
              let idToken = result["IdToken"] as? String else {
            // Check for challenges
            if let challenge = json?["ChallengeName"] as? String, challenge == "NEW_PASSWORD_REQUIRED" {
                throw AuthError.newPasswordRequired
            }
            throw AuthError.signInFailed("No token in response")
        }
        return idToken
    }
}

struct ConfigResponse: Codable {
    let user_pool_id: String
    let user_pool_client_id: String
    let region: String
    let username: String
    let password: String
}

enum AuthError: LocalizedError {
    case signInFailed(String)
    case newPasswordRequired

    var errorDescription: String? {
        switch self {
        case .signInFailed(let msg): "Sign in failed: \(msg)"
        case .newPasswordRequired: "Password change required. Sign in via the web UI first to set a new password, then use those credentials here."
        }
    }
}
