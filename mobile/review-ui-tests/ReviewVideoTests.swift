import XCTest

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

    private func wait(_ description: String, timeout: TimeInterval = 120,
                      _ condition: @escaping () -> Bool) {
        let predicate = NSPredicate { _, _ in condition() }
        let result = XCTWaiter.wait(for: [XCTNSPredicateExpectation(predicate: predicate, object: nil)],
                                   timeout: timeout)
        XCTAssertTrue(result == .completed, "\(step): \(description)")
    }

    private func shot(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = "review-\(name)"
        attachment.lifetime = .keepAlways
        add(attachment)
        // Give the review video readable pauses at each asserted state.
        Thread.sleep(forTimeInterval: 2)
    }

    private var composer: XCUIElement { app.webViews.textViews.firstMatch }
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
        signIn.tap()
        // ASWebAuthenticationSession's consent is owned by SpringBoard on some
        // iOS versions and by the app on others. Do not auto-accept other alerts.
        for host in [springboard, app] {
            let consent = host.alerts.firstMatch
            if consent.waitForExistence(timeout: 5),
               consent.staticTexts.allElementsBoundByIndex.contains(where: {
                   $0.label.localizedCaseInsensitiveContains("sign in")
               }), consent.buttons["Continue"].exists {
                consent.buttons["Continue"].tap()
                break
            }
        }
        let safari = XCUIApplication(bundleIdentifier: "com.apple.SafariViewService")
        var auth = app
        wait("system-browser email form") {
            for candidate in [self.app, safari, self.springboard] {
                if candidate.webViews.textFields.firstMatch.exists { auth = candidate; return true }
            }
            return false
        }
        let email = auth.webViews.textFields.firstMatch
        email.tap()
        email.typeText(username)
        let secure = auth.webViews.secureTextFields.firstMatch
        if !secure.exists {
            let next = auth.buttons.matching(NSPredicate(format: "label IN %@", ["Continue", "Next", "Sign in"])).firstMatch
            XCTAssertTrue(next.waitForExistence(timeout: 30), "email continuation missing")
            next.tap()
        }
        XCTAssertTrue(secure.waitForExistence(timeout: 60), "masked password field required")
        XCTAssertTrue(secure.elementType == .secureTextField, "never type password in a plain field")
        secure.tap()
        secure.typeText(password)
        let submit = auth.buttons.matching(NSPredicate(format: "label IN %@", ["Sign in", "Continue", "Log in"])).firstMatch
        XCTAssertTrue(submit.waitForExistence(timeout: 30), "password submit missing")
        submit.tap()
        wait("tinyassets callback returned to signed-in app", timeout: 180) {
            self.app.state == .runningForeground && self.app.buttons["Account"].exists
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
                && self.visibleText.contains(where: { $0.contains("app-store-review-sample.txt") })
        }
        shot("05-attached")
        _ = send(question, facts: ["orchard launch", "united states", "friday"])
        shot("05-grounded-answer")

        step = "06-account-privacy"
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
