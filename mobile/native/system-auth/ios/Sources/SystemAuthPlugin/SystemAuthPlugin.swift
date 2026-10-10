import AuthenticationServices
import Capacitor

@objc(SystemAuthPlugin)
public class SystemAuthPlugin: CAPPlugin, CAPBridgedPlugin, ASWebAuthenticationPresentationContextProviding {
    public let identifier = "SystemAuthPlugin"
    public let jsName = "SystemAuth"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "open", returnType: CAPPluginReturnPromise)
    ]
    private var session: ASWebAuthenticationSession?
    private var anchor: ASPresentationAnchor?

    @objc func open(_ call: CAPPluginCall) {
        guard let value = call.getString("url"), let url = URL(string: value),
              url.scheme == "https", url.host != nil, url.user == nil, url.password == nil else {
            call.reject("A secure sign-in URL is required.")
            return
        }
        DispatchQueue.main.async {
            guard self.session == nil else {
                call.reject("Sign-in is already open.")
                return
            }
            guard let window = self.bridge?.viewController?.view.window else {
                call.reject("The app window is unavailable. Try again.")
                return
            }
            self.anchor = window
            let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "tinyassets") { _, error in
                DispatchQueue.main.async {
                    self.session = nil
                    self.anchor = nil
                    if error != nil {
                        call.reject("Sign-in was cancelled. Try again when ready.")
                    } else {
                        // Callback data is not authority. JS redeems its saved reference with PKCE.
                        call.resolve()
                    }
                }
            }
            session.presentationContextProvider = self
            self.session = session
            if !session.start() {
                self.session = nil
                self.anchor = nil
                call.reject("The system sign-in browser could not open.")
            }
        }
    }

    public func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        // Set before start(), held until completion.
        return anchor!
    }
}
