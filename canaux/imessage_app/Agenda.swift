// Mode `KlodyMessages --agenda [jours]` : agenda et app Rappels en JSON sur stdout.
//
// Pourquoi dans la coque et pas en Python : TCC attribue l'accès Calendriers /
// Rappels au processus RESPONSABLE. Le canal (enfant de KlodyMessages.app)
// relance ce binaire ; le responsable reste l'app, seule à recevoir le droit —
// même raisonnement que l'Accès complet au disque (vécu le 2026-11-21). EventKit
// est lu localement : ni iCloud ni Google n'est interrogé par Klody.
//
// Sortie : {"evenements": [...], "rappels": [...], "erreurs": [...]} — un accès
// refusé n'est pas une panne, il est NOMMÉ dans `erreurs` et la section reste vide.
import EventKit
import Foundation

private func iso(_ d: Date?) -> String {
    guard let d = d else { return "" }
    let f = ISO8601DateFormatter()
    f.timeZone = TimeZone.current
    f.formatOptions = [.withInternetDateTime]
    return f.string(from: d)
}

func lancerAgenda(jours: Int) -> Never {
    let store = EKEventStore()
    let cal = Calendar.current
    let debut = cal.startOfDay(for: Date())
    let fin = cal.date(byAdding: .day, value: max(1, jours), to: debut)!
    var erreurs: [String] = []
    var evenements: [[String: Any]] = []
    var rappels: [[String: Any]] = []
    let sem = DispatchSemaphore(value: 0)

    var accesEvenements = false
    store.requestFullAccessToEvents { ok, err in
        accesEvenements = ok
        if let err = err { erreurs.append("calendriers : \(err.localizedDescription)") }
        sem.signal()
    }
    sem.wait()
    if accesEvenements {
        let pred = store.predicateForEvents(withStart: debut, end: fin, calendars: nil)
        for ev in store.events(matching: pred).sorted(by: { $0.startDate < $1.startDate }) {
            evenements.append([
                "id": ev.eventIdentifier ?? "",
                "titre": ev.title ?? "",
                "debut": iso(ev.startDate),
                "fin": iso(ev.endDate),
                "journee": ev.isAllDay,
                "lieu": ev.location ?? "",
                "calendrier": ev.calendar?.title ?? "",
            ])
        }
    } else if erreurs.isEmpty {
        erreurs.append("calendriers : accès refusé")
    }

    var accesRappels = false
    store.requestFullAccessToReminders { ok, err in
        accesRappels = ok
        if let err = err { erreurs.append("rappels : \(err.localizedDescription)") }
        sem.signal()
    }
    sem.wait()
    if accesRappels {
        // Non terminés, échéance avant la fin de la fenêtre (en retard compris).
        let pred = store.predicateForIncompleteReminders(
            withDueDateStarting: nil, ending: fin, calendars: nil)
        store.fetchReminders(matching: pred) { liste in
            for r in liste ?? [] {
                let echeance = r.dueDateComponents.flatMap { cal.date(from: $0) }
                let avecHeure = r.dueDateComponents?.hour != nil
                rappels.append([
                    "id": r.calendarItemIdentifier,
                    "titre": r.title ?? "",
                    "echeance": iso(echeance),
                    "avec_heure": avecHeure,
                    "liste": r.calendar?.title ?? "",
                ])
            }
            sem.signal()
        }
        sem.wait()
    } else {
        erreurs.append("rappels : accès refusé")
    }

    let sortie: [String: Any] = ["evenements": evenements, "rappels": rappels, "erreurs": erreurs]
    let data = try! JSONSerialization.data(withJSONObject: sortie, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    exit(0)
}
