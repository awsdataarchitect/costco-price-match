import Foundation

actor APIClient {
    static let shared = APIClient()
    private var baseURL: String { BackendConfig.shared.apiURL }

    private func token() async throws -> String {
        let t = BackendConfig.shared.idToken
        guard !t.isEmpty else { throw APIError.noToken }
        return t
    }

    private func request(_ method: String, _ path: String, body: Data? = nil) async throws -> Data {
        guard !baseURL.isEmpty else { throw APIError.notConnected }
        var req = URLRequest(url: URL(string: "\(baseURL)\(path)")!)
        req.httpMethod = method
        req.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        if let body { req.httpBody = body; req.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 {
            // Token expired, try refresh
            await BackendConfig.shared.refreshToken()
            let newToken = BackendConfig.shared.idToken
            guard !newToken.isEmpty else { throw APIError.http(401) }
            req.setValue("Bearer \(newToken)", forHTTPHeaderField: "Authorization")
            let (data2, resp2) = try await URLSession.shared.data(for: req)
            guard let http2 = resp2 as? HTTPURLResponse, 200..<300 ~= http2.statusCode else {
                throw APIError.http((resp2 as? HTTPURLResponse)?.statusCode ?? 0)
            }
            return data2
        }
        guard 200..<300 ~= code else { throw APIError.http(code) }
        return data
    }

    func get<T: Decodable>(_ path: String) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await request("GET", path))
    }

    func post<T: Decodable>(_ path: String, body: Encodable? = nil) async throws -> T {
        let data = try body.map { try JSONEncoder().encode($0) }
        return try JSONDecoder().decode(T.self, from: try await request("POST", path, body: data))
    }

    func delete(_ path: String) async throws {
        _ = try await request("DELETE", path)
    }

    func getRaw(_ path: String) async throws -> Data {
        try await request("GET", path)
    }

    func put<T: Decodable>(_ path: String, body: Encodable) async throws -> T {
        let data = try JSONEncoder().encode(body)
        return try JSONDecoder().decode(T.self, from: try await request("PUT", path, body: data))
    }

    func upload(pdf: Data, filename: String) async throws -> UploadResponse {
        guard !baseURL.isEmpty else { throw APIError.notConnected }
        let boundary = UUID().uuidString
        var req = URLRequest(url: URL(string: "\(baseURL)/api/upload")!)
        req.httpMethod = "POST"
        req.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\nContent-Type: application/pdf\r\n\r\n".data(using: .utf8)!)
        body.append(pdf)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body
        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw APIError.http((resp as? HTTPURLResponse)?.statusCode ?? 0)
        }
        return try JSONDecoder().decode(UploadResponse.self, from: data)
    }

    func analyzeStream(receiptIds: [String]? = nil) async throws -> URLSession.AsyncBytes {
        guard !baseURL.isEmpty else { throw APIError.notConnected }
        var path = "/api/analyze"
        if let ids = receiptIds, !ids.isEmpty {
            path += "?receipt_ids=\(ids.joined(separator: ","))"
        }
        var req = URLRequest(url: URL(string: "\(baseURL)\(path)")!)
        req.setValue("Bearer \(try await token())", forHTTPHeaderField: "Authorization")
        let (bytes, resp) = try await URLSession.shared.bytes(for: req)
        guard let http = resp as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw APIError.http((resp as? HTTPURLResponse)?.statusCode ?? 0)
        }
        return bytes
    }
}

enum APIError: LocalizedError {
    case noToken, notConnected, http(Int)
    var errorDescription: String? {
        switch self {
        case .noToken: "Not authenticated. Connect in Settings."
        case .notConnected: "No backend connected. Go to Settings to connect."
        case .http(let code): "HTTP error \(code)"
        }
    }
}
