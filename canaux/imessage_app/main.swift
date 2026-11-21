// KlodyMessages.app — coque de lancement du canal iMessage (canaux/imessage.py).
//
// Raison d'être : TCC attribue l'Accès complet au disque (lecture de chat.db) et
// l'Automatisation → Messages au *processus responsable*. Lancé directement par
// launchd, le Python du venv serait son propre responsable : il faudrait lui donner
// ces droits, donc à TOUT script de ce venv. Lancé comme enfant de ce bundle signé
// Developer ID, le responsable devient KlodyMessages.app — seule à recevoir les droits.
//
// Vécu le 2026-11-21 : l'accès accordé à Terminal.app ne couvrait pas une commande
// injectée depuis Claude.app ; TCC suit le responsable, pas le binaire. Même patron
// que KlodyShield.app (2026-10-19).
//
// Le lanceur ne fait rien d'autre : il démarre `python -m canaux.imessage`, relaie
// SIGTERM/SIGINT/SIGHUP à l'enfant et sort avec son code (launchd KeepAlive relance).
import Foundation

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write("KlodyMessages.app: \(msg)\n".data(using: .utf8)!)
    exit(78) // EX_CONFIG
}

let info = Bundle.main.infoDictionary ?? [:]
let env = ProcessInfo.processInfo.environment
guard let repo = env["KLODY_IMSG_REPO"] ?? info["KlodyRepo"] as? String else {
    fail("clé KlodyRepo absente de Info.plist")
}
let python = env["KLODY_IMSG_PYTHON"] ?? "\(repo)/.venv/bin/python"
guard FileManager.default.fileExists(atPath: "\(repo)/canaux/imessage.py") else {
    fail("dépôt introuvable : \(repo)")
}
guard FileManager.default.isExecutableFile(atPath: python) else {
    fail("interpréteur introuvable : \(python)")
}

var args = ["-m", "canaux.imessage"]
args += CommandLine.arguments.dropFirst()  // ex. --probe, --dry-run

let child = Process()
child.executableURL = URL(fileURLWithPath: python)
child.arguments = args
child.currentDirectoryURL = URL(fileURLWithPath: repo)
var childEnv = env
childEnv["PYTHONPATH"] = repo
childEnv["PYTHONUNBUFFERED"] = "1"
childEnv["KLODY_IMSG_LAUNCHER"] = Bundle.main.bundlePath
child.environment = childEnv

var sources: [DispatchSourceSignal] = []
for sig in [SIGTERM, SIGINT, SIGHUP] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler {
        if child.isRunning { kill(child.processIdentifier, sig) }
    }
    src.resume()
    sources.append(src)
}

child.terminationHandler = { p in
    let code: Int32 = p.terminationReason == .uncaughtSignal ? 128 + p.terminationStatus : p.terminationStatus
    exit(code)
}

do {
    try child.run()
} catch {
    fail("lancement de \(python) impossible : \(error)")
}
dispatchMain()
