// Modes `KlodyMessages --creer-rappel …` et `--creer-evenement …` : ÉCRITURE
// dans l'app Rappels / Calendrier (EventKit), réponse JSON sur stdout ou --sortie.
//
// Même raison d'être que Agenda.swift : TCC attribue Calendriers / Rappels au
// processus RESPONSABLE, seule cette app signée a le droit. Les appelants Python
// (tools/agenda.py) ne lancent ce mode qu'APRÈS le « oui » de l'utilisateur
// (canal iMessage) ou la validation de l'app (WebSocket) — demandé le 2026-11-26,
// chantier « Actions avec ton oui ».
//
// Arguments (tous en argv, jamais interprétés par un shell) :
//   --titre T  [--notes N]
//   rappel    : [--echeance AAAA-MM-JJ | AAAA-MM-JJTHH:MM:SS±HH:MM] [--liste L]
//   événement : --debut ISO --fin ISO [--journee] [--lieu L] [--calendrier C]
// Sortie : {"ok": true, "id": …, "titre": …, "liste": …} ou {"ok": false, "erreur": …}.
import EventKit
import Foundation

/// Lecture SÉQUENTIELLE clé → valeur à partir de a[2] : une valeur qui ressemble
/// à un drapeau (titre « --journee », notes « --sortie /tmp/x ») reste une
/// valeur. Un `firstIndex(of:)` la prendrait pour le drapeau.
private func analyser(_ a: [String]) -> (valeurs: [String: String], drapeaux: Set<String>) {
    var valeurs: [String: String] = [:]
    var drapeaux: Set<String> = []
    var i = 2
    while i < a.count {
        if a[i] == "--journee" {
            drapeaux.insert(a[i])
            i += 1
        } else {
            if i + 1 < a.count, valeurs[a[i]] == nil { valeurs[a[i]] = a[i + 1] }
            i += 2
        }
    }
    return (valeurs, drapeaux)
}

private func dateISO(_ s: String) -> Date? {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: s)
}

private func ecrire(_ obj: [String: Any], _ cheminSortie: String?) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    if let c = cheminSortie {
        do {
            try data.write(to: URL(fileURLWithPath: c), options: [.atomic])
        } catch {
            FileHandle.standardError.write("KlodyMessages.app: écriture \(c) : \(error)\n".data(using: .utf8)!)
            exit(73) // EX_CANTCREAT
        }
    } else {
        FileHandle.standardOutput.write(data)
    }
    exit(0)
}

private func echec(_ msg: String, _ sortie: String?) -> Never {
    ecrire(["ok": false, "erreur": msg], sortie)
}

/// Calendrier nommé (titre exact, sans casse) parmi ceux qui ACCEPTENT l'écriture,
/// sinon le calendrier par défaut. Un nom inconnu est une erreur nommée, pas un
/// repli silencieux : l'utilisateur a validé « dans la liste Courses ».
private func calendrier(_ store: EKEventStore, _ type: EKEntityType, _ nom: String?,
                        _ defaut: EKCalendar?) -> (EKCalendar?, String?) {
    let n = (nom ?? "").trimmingCharacters(in: .whitespaces)
    if n.isEmpty {
        guard let d = defaut else { return (nil, "aucun calendrier par défaut") }
        return (d, nil)
    }
    let ok = store.calendars(for: type).filter { $0.allowsContentModifications }
    if let c = ok.first(where: { $0.title.compare(n, options: .caseInsensitive) == .orderedSame }) {
        return (c, nil)
    }
    return (nil, "« \(n) » introuvable (ou en lecture seule)")
}

func lancerCreation(_ a: [String]) -> Never {
    let (v, drapeaux) = analyser(a)
    let valeur: ([String], String) -> String? = { _, cle in v[cle] }
    let sortie = valeur(a, "--sortie")
    if let c = sortie, !sortieAutorisee(c) {
        FileHandle.standardError.write("KlodyMessages.app: --sortie hors de ~/.klody/data/agenda refusée\n".data(using: .utf8)!)
        exit(64) // EX_USAGE
    }
    let rappel = a[1] == "--creer-rappel"
    let titre = (valeur(a, "--titre") ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    if titre.isEmpty { echec("titre vide", sortie) }
    let notes = valeur(a, "--notes")

    let store = EKEventStore()
    let sem = DispatchSemaphore(value: 0)
    var acces = false
    var erreurAcces: String? = nil
    let fin: (Bool, Error?) -> Void = { ok, err in
        acces = ok
        erreurAcces = err?.localizedDescription
        sem.signal()
    }
    if rappel { store.requestFullAccessToReminders(completion: fin) }
    else { store.requestFullAccessToEvents(completion: fin) }
    sem.wait()
    if !acces {
        echec((rappel ? "rappels" : "calendriers") + " : " + (erreurAcces ?? "accès refusé"), sortie)
    }

    if rappel {
        let r = EKReminder(eventStore: store)
        r.title = titre
        if let n = notes, !n.isEmpty { r.notes = n }
        let (c, m) = calendrier(store, .reminder, valeur(a, "--liste"), store.defaultCalendarForNewReminders())
        guard let liste = c else { echec("liste " + (m ?? "?"), sortie) }
        r.calendar = liste
        if let e = valeur(a, "--echeance"), !e.isEmpty {
            let cal = Calendar.current
            if e.count == 10 {
                // Date seule : pas d'heure, donc pas d'alerte (comme dans l'app Rappels).
                let p = e.split(separator: "-").compactMap { Int($0) }
                guard p.count == 3 else { echec("échéance illisible : \(e)", sortie) }
                r.dueDateComponents = DateComponents(year: p[0], month: p[1], day: p[2])
            } else {
                guard let d = dateISO(e) else { echec("échéance illisible : \(e)", sortie) }
                r.dueDateComponents = cal.dateComponents([.year, .month, .day, .hour, .minute], from: d)
                // Une échéance avec heure sans alarme ne sonne pas : on l'ajoute.
                r.addAlarm(EKAlarm(absoluteDate: d))
            }
        }
        do { try store.save(r, commit: true) } catch { echec("enregistrement : \(error.localizedDescription)", sortie) }
        ecrire(["ok": true, "id": r.calendarItemIdentifier, "titre": titre,
                "liste": r.calendar?.title ?? ""], sortie)
    }

    guard let d = dateISO(valeur(a, "--debut") ?? ""), let f = dateISO(valeur(a, "--fin") ?? "") else {
        echec("début ou fin illisible", sortie)
    }
    if f < d { echec("fin avant le début", sortie) }
    let ev = EKEvent(eventStore: store)
    ev.title = titre
    ev.startDate = d
    ev.endDate = f
    ev.isAllDay = drapeaux.contains("--journee")
    if let l = valeur(a, "--lieu"), !l.isEmpty { ev.location = l }
    if let n = notes, !n.isEmpty { ev.notes = n }
    let (c, m) = calendrier(store, .event, valeur(a, "--calendrier"), store.defaultCalendarForNewEvents)
    guard let cal = c else { echec("calendrier " + (m ?? "?"), sortie) }
    ev.calendar = cal
    do { try store.save(ev, span: .thisEvent, commit: true) } catch {
        echec("enregistrement : \(error.localizedDescription)", sortie)
    }
    ecrire(["ok": true, "id": ev.eventIdentifier ?? "", "titre": titre,
            "liste": ev.calendar?.title ?? ""], sortie)
}
