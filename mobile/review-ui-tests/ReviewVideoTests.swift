import XCTest
import UIKit

// The same journey runs on Simulator and a physical Device Farm iPhone.
// No API shortcuts, injected cookies, model stubs or account-deletion actions.
final class ReviewVideoTests: XCTestCase {
    private let app = XCUIApplication(bundleIdentifier: "io.tinyassets.app")
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    private let bakery = "Give me a three-step plan to launch a small bakery's online orders, and keep it short."
    private let question = "What is the project, target storefront, and review deadline in this file?"
    private var step = "01-launch"

    override func setUpWithError() throws {
        continueAfterFailure = false
        executionTimeAllowance = 1500
        XCUIDevice.shared.orientation = .portrait
        let sample = try XCTUnwrap(Bundle(for: Self.self).url(
            forResource: "app-store-review-sample", withExtension: "txt"))
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        try FileManager.default.createDirectory(at: documents, withIntermediateDirectories: true)
        try Data(contentsOf: sample).write(to: documents.appendingPathComponent(sample.lastPathComponent))
    }

    private func poll(timeout: TimeInterval, _ condition: @escaping () -> Bool) -> Bool {
        let predicate = NSPredicate { _, _ in condition() }
        return XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: nil)],
                              timeout: timeout) == .completed
    }

    private func wait(_ description: String, timeout: TimeInterval = 120,
                      _ condition: @escaping () -> Bool) {
        XCTAssertTrue(poll(timeout: timeout, condition), "\(step): \(description)")
    }

    private func shot(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "review-\(name)"
        attachment.lifetime = .keepAlways
        add(attachment)
        // Give the review video readable pauses at each asserted state.
        Thread.sleep(forTimeInterval: 2)
    }

    private var composer: XCUIElement {
        app.webViews.textViews.matching(NSPredicate(format: "label BEGINSWITH %@", "Message ")).firstMatch
    }
    private var visibleText: [String] { app.webViews.staticTexts.allElementsBoundByIndex.map(\.label) }

    private func openChat() {
        let bubble = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Open the chat with your agent")).firstMatch
        if bubble.waitForExistence(timeout: 5), bubble.isHittable { bubble.tap() }
        wait("signed-in conversation composer") { self.composer.exists && self.composer.isHittable }
    }

    private func answer(after prompt: String) -> String {
        let labels = visibleText
        guard let start = labels.lastIndex(where: { $0.contains(prompt) }) else { return "" }
        // WebKit exposes rendered text in document order. The current user's
        // message must precede the answer; old answers cannot satisfy this test.
        let following = Array(labels.dropFirst(start + 1))
        guard let author = following.firstIndex(of: "Command center") else { return "" }
        // Skip the author and its timestamp; only the reply body is compared.
        return following.dropFirst(author + 2).prefix(while: {
            !$0.hasPrefix("Next message:") && $0 != "Conversation status"
                && $0 != "You" && $0 != "Notice" && $0 != "Platform notice"
        }).joined(separator: "\n")
    }

    private func send(_ prompt: String, facts: [String]) -> String {
        let before = visibleText.filter { $0.contains(prompt) }.count
        composer.tap()
        composer.typeText(prompt)
        app.buttons["Send"].tap()
        wait("new user message accepted") {
            self.visibleText.filter { $0.contains(prompt) }.count > before
        }
        var last = ""
        var stableSince = Date()
        wait("complete substantive reply (working model connection required)", timeout: 480) {
            let text = self.answer(after: prompt)
            if text != last { last = text; stableSince = Date() }
            let lower = text.lowercased()
            let minimumLength = facts.count == 3 ? 40 : 100
            return text.count >= minimumLength && facts.allSatisfy { lower.contains($0) }
                && !self.app.buttons["Stop your agent's current turn"].exists
                && self.app.buttons["Send"].exists
                && Date().timeIntervalSince(stableSince) >= 10
        }
        return last
    }

    /// Taps Sign in and returns the system browser showing the email form.
    /// iOS 26: SpringBoard asks "“App” Wants to Use “<site>” to Sign In" only
    /// after the app has fetched its sign-in URL, which can take longer than a
    /// fixed alert wait. Accept exactly that consent whenever it appears, and
    /// find the form in SafariViewService, which hosts ASWebAuthenticationSession.
    private func openSystemSignIn(_ signIn: XCUIElement) -> XCUIApplication {
        let safari = XCUIApplication(bundleIdentifier: "com.apple.SafariViewService")
        signIn.tap()
        var auth = safari
        let found = poll(timeout: 120) {
            for host in [self.springboard, self.app] {
                let consent = host.alerts.matching(NSPredicate(
                    format: "label CONTAINS %@ AND label CONTAINS[c] %@", "Wants to Use", "Sign In")).firstMatch
                if consent.exists, consent.buttons["Continue"].exists { consent.buttons["Continue"].tap() }
            }
            for candidate in [safari, self.app] {
                if candidate.webViews.textFields.firstMatch.exists { auth = candidate; return true }
            }
            return false
        }
        if !found {
            XCTFail("\(step): system-browser email form. Hierarchy: \(hierarchy([springboard, app, safari]))")
        }
        return auth
    }

    /// One-line accessibility tree for a failure message (the harness exports
    /// only assertion lines). Element values are omitted, so typed text never
    /// appears, and every string in `secrets` is replaced as well.
    private func hierarchy(_ hosts: [XCUIApplication], redacting secrets: [String] = []) -> String {
        var lines: [String] = []
        for host in hosts {
            let state = host.state
            guard state == .runningForeground || state == .runningBackground else { continue }
            for line in host.debugDescription.components(separatedBy: "\n") {
                if let cut = line.range(of: ", value: ") {
                    lines.append(String(line[..<cut.lowerBound]) + ", value: [omitted]")
                } else {
                    lines.append(line)
                }
            }
        }
        var flat = lines.joined(separator: " | ")
        // Whole secrets first, then their pieces: a keyboard suggestion bar can
        // show an email's local part or domain on its own.
        let pieces = secrets.flatMap { $0.components(separatedBy: CharacterSet(charactersIn: "@.+")) }
            .filter { $0.count >= 4 }
        for secret in secrets + pieces where !secret.isEmpty {
            flat = flat.replacingOccurrences(of: secret, with: "[REDACTED]", options: .caseInsensitive)
            let unreserved = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
            if let encoded = secret.addingPercentEncoding(withAllowedCharacters: unreserved) {
                flat = flat.replacingOccurrences(of: encoded, with: "[REDACTED]", options: .caseInsensitive)
            }
        }
        return flat
    }

    func testSignedOutLaunch() {
        app.terminate()
        XCTAssertTrue(app.state == .notRunning)
        app.launch()
        let signIn = app.buttons["Sign in"]
        XCTAssertTrue(signIn.waitForExistence(timeout: 120))
        shot("01-signed-out")
        // Credential-free: prove the system sign-in sheet opens; type nothing.
        step = "02-sign-in"
        _ = openSystemSignIn(signIn)
    }

    func testGuideline21Journey() throws {
        let env = ProcessInfo.processInfo.environment
        let username = try XCTUnwrap(env["APP_STORE_REVIEW_USERNAME"], "review username missing")
        let password = try XCTUnwrap(env["APP_STORE_REVIEW_PASSWORD"], "review password missing")
        XCTAssertFalse(username.isEmpty, "review username empty")
        XCTAssertFalse(password.isEmpty, "review password empty")

        // A clean installation is supplied by the harness. Video starts before
        // launch; the harness separately asserts the native splash in its frames.
        app.terminate()
        XCTAssertTrue(app.state == .notRunning, "cold launch required")
        app.launch()
        let signIn = app.buttons["Sign in"]
        XCTAssertTrue(signIn.waitForExistence(timeout: 120), "fresh install must be signed out")
        shot("01-signed-out")

        step = "02-sign-in"
        let auth = openSystemSignIn(signIn)
        let safari = XCUIApplication(bundleIdentifier: "com.apple.SafariViewService")
        let email = auth.webViews.textFields.firstMatch
        let secure = auth.webViews.secureTextFields.firstMatch
        // AuthKit labels its email step "Continue with email". On iOS 26 the
        // sheet lives in the app's own tree, beside the app's disabled
        // "Sign in" button, so only enabled buttons count.
        let next = auth.buttons.matching(NSPredicate(format: "label IN %@ AND enabled == true",
            ["Continue with email", "Continue", "Next", "Sign in"])).firstMatch
        let go = auth.keyboards.buttons.matching(NSPredicate(format: "label IN %@",
            ["Go", "go", "Continue", "continue", "Return", "return", "Done", "done"])).firstMatch
        // The page can re-render after the first keystrokes and drop them, and
        // a submit can land on the keyboard accessory instead. While the email
        // page is up, make the field hold exactly the username (compared here,
        // never reported), then submit with the button and then the Go key.
        for _ in 0..<4 where !secure.exists {
            var typed = email.value as? String ?? ""
            if typed == email.placeholderValue { typed = "" }
            if typed.caseInsensitiveCompare(username) != .orderedSame {
                // Tap the right edge so the cursor sits after any existing text.
                email.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
                if !typed.isEmpty {
                    email.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: typed.count))
                }
                email.typeText(username)
            }
            XCTAssertTrue(next.waitForExistence(timeout: 30), "email continuation missing")
            next.tap()
            if poll(timeout: 15, { secure.exists }) { break }
            if go.exists { go.tap() }
            if poll(timeout: 10, { secure.exists }) { break }
        }
        if !secure.waitForExistence(timeout: 60) {
            // The username is typed, the password is not. Values are omitted
            // and the username is redacted; the harness redacts it again.
            XCTFail("\(step): masked password field required. Hierarchy: "
                + hierarchy([auth, springboard], redacting: [username]))
        }
        XCTAssertTrue(secure.elementType == .secureTextField, "never type password in a plain field")
        secure.tap()
        // Per-key typing can expose the last character in iOS password fields.
        // Paste into the asserted secure field, then immediately clear the board.
        UIPasteboard.general.string = password
        defer { UIPasteboard.general.items = [] }
        secure.press(forDuration: 1.2)
        let paste = auth.menuItems["Paste"]
        let pasteButton = auth.buttons["Paste"]
        wait("secure-field Paste action") { paste.exists || pasteButton.exists }
        if paste.exists { paste.tap() } else { pasteButton.tap() }
        for host in [app, springboard, safari] {
            let allowPaste = host.alerts.buttons["Allow Paste"]
            if allowPaste.waitForExistence(timeout: 2) { allowPaste.tap(); break }
        }
        wait("masked password was entered") {
            let value = secure.value as? String ?? ""
            return value.count == password.count && value.allSatisfy { $0 == "•" || $0 == "●" }
        }
        UIPasteboard.general.items = []
        let submit = auth.buttons.matching(NSPredicate(format: "label IN %@ AND enabled == true",
            ["Sign in", "Continue", "Log in"])).firstMatch
        XCTAssertTrue(submit.waitForExistence(timeout: 30), "password submit missing")
        submit.tap()
        // A tap swallowed by the keyboard accessory leaves the password page
        // up: submit with the Go key instead.
        if !poll(timeout: 15, { !secure.exists }), go.exists { go.tap() }
        let signedIn = poll(timeout: 180) {
            self.app.state == .runningForeground && (
                self.app.buttons["Command center menu"].exists ||
                self.app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Open the chat with your agent")).firstMatch.exists
            )
        }
        if !signedIn {
            // A page after the password (code, consent) or the app's sign-in
            // notice. Values are omitted; both credentials are redacted.
            XCTFail("\(step): tinyassets callback returned to signed-in app. Hierarchy: "
                + hierarchy([app, auth, springboard], redacting: [username, password]))
        }
        shot("02-signed-in")

        step = "03-universe"
        openChat()
        XCTAssertFalse(app.buttons["Sign in"].exists, "still signed out")
        shot("03-universe")

        step = "04-conversation"
        let reply = send(bakery, facts: ["order"])
        shot("04-reply")
        app.terminate()
        XCTAssertTrue(app.state == .notRunning, "force quit did not terminate")
        app.launch()
        openChat()
        wait("same prompt and full reply persist after relaunch") {
            self.answer(after: self.bakery) == reply
        }
        shot("04-persisted")

        step = "05-file"
        app.buttons["Attach a file"].tap()
        let choose = app.buttons["Choose File"]
        if choose.waitForExistence(timeout: 3) { choose.tap() }
        // UIDocumentPicker can initially show Recents or the previous folder.
        let browse = app.buttons["Browse"]
        if browse.waitForExistence(timeout: 5) { browse.tap() }
        if browse.exists && browse.isHittable { browse.tap() }
        let onPhone = app.staticTexts["On My iPhone"]
        XCTAssertTrue(onPhone.waitForExistence(timeout: 30), "Files local storage missing")
        onPhone.tap()
        let folder = app.staticTexts["Review Files"]
        XCTAssertTrue(folder.waitForExistence(timeout: 30), "runner's shared sample folder missing")
        folder.tap()
        let file = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "app-store-review-sample")).firstMatch
        XCTAssertTrue(file.waitForExistence(timeout: 30), "sample file missing")
        file.tap()
        let open = app.buttons["Open"]
        if open.waitForExistence(timeout: 3), open.isHittable { open.tap() }
        wait("sample uploaded through the app") {
            self.app.buttons["Remove app-store-review-sample.txt"].exists
                && self.visibleText.contains(where: { $0.contains("app-store-review-sample.txt (") })
                && !self.visibleText.contains(where: { $0.contains("app-store-review-sample.txt —") })
                && !self.app.buttons["Check app-store-review-sample.txt"].exists
        }
        shot("05-attached")
        _ = send(question, facts: ["orchard launch", "united states", "friday"])
        shot("05-grounded-answer")

        step = "06-account-privacy"
        app.buttons["Command center menu"].tap()
        XCTAssertTrue(app.buttons["Account"].waitForExistence(timeout: 15), "Account menu entry missing")
        app.buttons["Account"].tap()
        let delete = app.buttons["Delete my account"]
        for _ in 0..<25 {
            if delete.exists && delete.isHittable { break }
            app.swipeUp()
        }
        XCTAssertTrue(delete.exists && delete.isHittable, "deletion path not visible")
        XCTAssertFalse(delete.isEnabled, "deletion must remain unconfirmed")
        let confirmation = app.webViews.textFields.matching(NSPredicate(format: "label CONTAINS %@", "Type DELETE")).firstMatch
        XCTAssertTrue(confirmation.exists, "deletion confirmation field missing")
        XCTAssertTrue((confirmation.value as? String ?? "").isEmpty, "deletion confirmation must stay empty")
        XCTAssertTrue(app.links["Privacy policy"].exists, "privacy navigation missing")
        shot("06-account-privacy")
        // Deliberately never type DELETE or tap the destructive button.
    }
}
