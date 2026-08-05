"""
Génère un crâne humain stylisé en Blender, par opérations booléennes.

    blender --background --python skull_generator.py \
        --render-output /chemin/crane_ --render-frame 1

Repère : +X à droite, **+Y vers l'AVANT (la face)**, +Z vers le haut.

⚠️ Trois pièges vécus le 2026-08-05, chacun ayant produit un rendu qui
RÉUSSISSAIT (exit 0) tout en montrant autre chose qu'un crâne :

  1. **Un creux se creuse, il ne se pose pas.** La version d'origine posait
     orbites, nez et dents comme des objets SÉPARÉS *à l'intérieur* du volume
     (d² = 0,47 pour les orbites, 0,75 pour le nez), sans aucun booléen : seule
     la coque extérieure était visible, d'où un ovoïde lisse. Ici chaque trait
     est un OUTIL de coupe qui doit TRAVERSER la surface — condition vérifiée
     par `verifier_traversees()`, qui fait échouer le script si elle se perd.
  2. **La caméra regardait l'occiput.** Elle était en (3, −4, 2) alors que la
     face est en +Y. Deux causes indépendantes, un seul symptôme.
  3. **Un tore ENTIER fait un tonneau, pas une arcade.** L'anneau arrière
     ressortait sous l'occiput : le crâne semblait posé sur un tambour. Les
     arcades sont tranchées par `y_min`.

Le corps est UN volume continu (boîte crânienne ∪ masse faciale ∪ arcades
zygomatiques), pas deux boules accolées — accoler donnait un museau détaché.
"""

import math
import sys

import bpy

# ====== GÉOMÉTRIE DE RÉFÉRENCE ======
# Tout le reste se positionne par rapport à ces deux volumes ; les traits sont
# placés à partir de la SURFACE calculée, jamais par tâtonnement.
CRANE_C = (0.00, 0.00, 0.30)      # boîte crânienne : centre
CRANE_A = (0.60, 0.68, 0.50)      #                   demi-axes
FACE_C = (0.00, 0.16, -0.22)      # masse faciale : centre
FACE_A = (0.58, 0.60, 0.54)       #                 demi-axes

ORBITE_X = 0.30                   # écartement des orbites
ORBITE_Z = 0.08
ORBITE_Y = 0.48                   # centre du cône de coupe
ORBITE_PROF = 0.58

NEZ_Y = 0.62
NEZ_Z = -0.33
NEZ_RAYON = 0.125
NEZ_ETIREMENT_Y = 3.0             # garantit la traversée (cf. outil_nez)

ARCADE_SUP_Y = 0.20               # arcade dentaire supérieure : centre
ARCADE_SUP_Z = -0.70
ARCADE_SUP_R = 0.30

ZYGO_X = 0.42                     # pommette : centre du renflement
ZYGO_Y = 0.30
ZYGO_Z = -0.16
ZYGO_A = (0.16, 0.22, 0.10)       # demi-axes — doit AFFLEURER, pas percer

MANDIBULE_Y = 0.16
MANDIBULE_Z = -0.94
MANDIBULE_R = 0.32
BRANCHE_X = 0.295                 # branche montante : ancrage sur l'arc
DENTS_INF_Z = -0.82


def _norme_ellipsoide(p, centre, demi_axes):
    """d² d'un point dans le repère de l'ellipsoïde : < 1 = intérieur."""
    return sum(((p[i] - centre[i]) / demi_axes[i]) ** 2 for i in range(3))


def _surface_avant(x, z, centre, demi_axes):
    """Ordonnée y de la surface de l'ellipsoïde, à (x, z) donnés.

    C'est CE nombre qui dit jusqu'où un outil doit aller pour couper. Renvoie
    None si (x, z) est hors de la section — l'outil n'a alors rien à trancher.
    """
    reste = 1.0 - ((x - centre[0]) / demi_axes[0]) ** 2 - ((z - centre[2]) / demi_axes[2]) ** 2
    if reste <= 0.0:
        return None
    return centre[1] + demi_axes[1] * math.sqrt(reste)


def purger_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials):
        for bloc in list(collection):
            if bloc.users == 0:
                collection.remove(bloc)


def _activer(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def figer_transformations(obj):
    """Applique échelle et rotation dans le maillage.

    Un booléen EXACT entre objets d'échelles différentes produit des artefacts
    de tolérance ; on fige avant de couper.
    """
    _activer(obj)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def booleen(cible, outil, operation="DIFFERENCE"):
    """Applique `outil` sur `cible`, puis SUPPRIME l'outil.

    L'outil est consommé : le laisser dans la scène le rendrait visible au
    rendu — c'est exactement ce qui donnait des sphères flottantes.
    """
    figer_transformations(outil)
    _activer(cible)
    mod = cible.modifiers.new(name="decoupe", type="BOOLEAN")
    mod.object = outil
    mod.operation = operation
    mod.solver = "EXACT"
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(outil, do_unlink=True)
    return cible


def ellipsoide(nom, centre, demi_axes, segments=96, anneaux=48):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments, ring_count=anneaux, radius=1.0, location=centre
    )
    obj = bpy.context.active_object
    obj.name = nom
    obj.scale = demi_axes
    figer_transformations(obj)
    return obj


# ====== OUTILS DE COUPE ======


def outil_orbite(cote):
    """Cône de coupe d'une orbite : base large à l'AVANT, pointe vers l'arrière.

    La rotation de 90° autour de X envoie l'axe +Z du cône sur −Y : la base
    (radius1, en −Z) se retrouve donc en +Y, face à l'observateur. Le lacet de
    ±13° écarte les orbites, l'assiette de −8° les incline vers le bas.
    """
    bpy.ops.mesh.primitive_cone_add(
        vertices=64,
        radius1=0.205,
        radius2=0.060,
        depth=ORBITE_PROF,
        location=(cote * ORBITE_X, ORBITE_Y, ORBITE_Z),
        rotation=(math.radians(82), 0.0, math.radians(cote * 13)),
    )
    outil = bpy.context.active_object
    outil.name = f"Outil_Orbite_{'D' if cote > 0 else 'G'}"
    outil.scale = (1.0, 1.0, 0.88)  # orbites plus larges que hautes
    figer_transformations(outil)
    return outil


def outil_nez():
    """Ouverture piriforme : cône vertical, large en bas, effilé en haut.

    Vue de face, un cône d'axe vertical projette un triangle — c'est la forme
    de l'ouverture nasale. L'étirement en Y sert UNIQUEMENT à garantir la
    traversée : sans lui le cône affleure et ne creuse rien.
    """
    bpy.ops.mesh.primitive_cone_add(
        vertices=48,
        radius1=NEZ_RAYON,
        radius2=0.022,
        depth=0.32,
        location=(0.0, NEZ_Y, NEZ_Z),
    )
    outil = bpy.context.active_object
    outil.name = "Outil_Nez"
    outil.scale = (1.0, NEZ_ETIREMENT_Y, 1.0)
    figer_transformations(outil)
    return outil


def outil_fosse_temporale(cote):
    """Léger galbe sur l'écaille du temporal.

    ⚠️ Une sphère trop enfoncée ne creuse pas un galbe, elle tranche un PLAN :
    une version antérieure taillait une facette plate bien visible. L'outil
    doit EFFLEURER la surface, pas la couper de part en part.
    """
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=48, ring_count=24, radius=0.34, location=(cote * 0.90, 0.10, 0.30)
    )
    outil = bpy.context.active_object
    outil.name = f"Outil_Fosse_{'D' if cote > 0 else 'G'}"
    outil.scale = (0.60, 1.00, 0.80)
    figer_transformations(outil)
    return outil


def arcade_dentaire(nom, centre_y, z, rayon, epaisseur, y_min):
    """Arc dentaire taillé dans un tore.

    ⚠️ Un tore ENTIER donne un tonneau : l'anneau arrière ressort sous
    l'occiput. `y_min` tranche l'arrière et ne garde que le U.
    """
    bpy.ops.mesh.primitive_torus_add(
        major_radius=rayon,
        minor_radius=epaisseur,
        major_segments=72,
        minor_segments=20,
        location=(0.0, centre_y, z),
    )
    obj = bpy.context.active_object
    obj.name = nom
    bpy.ops.mesh.primitive_cube_add(size=4.0, location=(0.0, y_min - 2.0, z))
    booleen(obj, bpy.context.active_object)
    return obj


def outil_sillons(centre_y, z, rayon, nb_dents=14, ouverture=140.0):
    """Un SEUL outil pour tous les sillons interdentaires.

    Quatorze booléens séparés coûteraient quatorze évaluations du solveur
    EXACT ; on fusionne les lames en un objet, puis on coupe une fois.
    """
    lames = []
    for i in range(nb_dents + 1):
        angle = math.radians(-ouverture / 2 + i * ouverture / nb_dents)
        bpy.ops.mesh.primitive_cube_add(
            size=1.0,
            location=(rayon * math.sin(angle), centre_y + rayon * math.cos(angle), z),
            rotation=(0.0, 0.0, -angle),
        )
        lame = bpy.context.active_object
        lame.scale = (0.020, 0.24, 0.26)
        figer_transformations(lame)
        lames.append(lame)

    _activer(lames[0])
    for lame in lames:
        lame.select_set(True)
    bpy.context.view_layer.objects.active = lames[0]
    bpy.ops.object.join()
    outil = bpy.context.active_object
    outil.name = "Outil_Sillons"
    return outil


# ====== CONSTRUCTION ======


def construire_crane():
    """UN volume continu, puis on CREUSE les traits dedans."""
    crane = ellipsoide("Crane", CRANE_C, CRANE_A)
    face = ellipsoide("Face", FACE_C, FACE_A, segments=80, anneaux=40)
    booleen(crane, face, operation="UNION")

    # Arcades zygomatiques (pommettes) : cylindres d'axe Y reliant la face aux
    # tempes. Elles ferment le volume sur les côtés — sans elles la jonction
    # crâne/face se lit comme un museau rapporté.
    for cote in (-1, 1):
        pommette = ellipsoide(
            f"Pommette_{'D' if cote > 0 else 'G'}",
            (cote * ZYGO_X, ZYGO_Y, ZYGO_Z),
            ZYGO_A,
            segments=40,
            anneaux=20,
        )
        booleen(crane, pommette, operation="UNION")

    # Arcade dentaire supérieure, unie AVANT le creusement des sillons.
    dents = arcade_dentaire(
        "Dents_Sup", ARCADE_SUP_Y, ARCADE_SUP_Z, ARCADE_SUP_R, 0.075, y_min=0.06
    )
    booleen(crane, dents, operation="UNION")
    booleen(crane, outil_sillons(ARCADE_SUP_Y, ARCADE_SUP_Z, ARCADE_SUP_R))

    # Creusements.
    for cote in (-1, 1):
        booleen(crane, outil_orbite(cote))
        booleen(crane, outil_fosse_temporale(cote))
    booleen(crane, outil_nez())

    crane.name = "Crane"
    return crane


def construire_mandibule():
    """Mâchoire inférieure : arc en U + deux branches montantes."""
    machoire = arcade_dentaire(
        "Mandibule", MANDIBULE_Y, MANDIBULE_Z, MANDIBULE_R, 0.095, y_min=-0.10
    )

    # Branches montantes vers l'articulation temporo-mandibulaire.
    # ⚠️ Elles doivent naître SUR l'arc : plantées au-delà de |x| = MANDIBULE_R
    # elles flottaient dans le vide, et le rendu montrait deux boîtes détachées.
    for cote in (-1, 1):
        bpy.ops.mesh.primitive_cube_add(
            size=1.0,
            location=(cote * BRANCHE_X, 0.00, -0.68),
            rotation=(math.radians(-13), 0.0, 0.0),
        )
        branche = bpy.context.active_object
        branche.name = f"Branche_{'D' if cote > 0 else 'G'}"
        branche.scale = (0.080, 0.145, 0.44)
        booleen(machoire, branche, operation="UNION")

    dents = arcade_dentaire(
        "Dents_Inf", MANDIBULE_Y, DENTS_INF_Z, MANDIBULE_R - 0.025, 0.065, y_min=0.00
    )
    booleen(machoire, dents, operation="UNION")
    booleen(machoire, outil_sillons(MANDIBULE_Y, DENTS_INF_Z, MANDIBULE_R - 0.025))

    machoire.name = "Mandibule"
    return machoire


# ====== MATÉRIAUX, SCÈNE ======


def materiau_os(nom, couleur, rugosite):
    """⚠️ Ne PAS toucher `use_nodes` : déprécié, retiré en Blender 6.0.
    Les matériaux naissent déjà avec un arbre de nœuds depuis la 4.x."""
    mat = bpy.data.materials.new(name=nom)
    if mat.node_tree is None:  # filet pour une version plus ancienne
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new(type="ShaderNodeBsdfPrincipled")
        sortie = mat.node_tree.nodes.get("Material Output")
        mat.node_tree.links.new(bsdf.outputs["BSDF"], sortie.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = couleur
    bsdf.inputs["Roughness"].default_value = rugosite
    bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def lisser(obj, angle_deg=30.0):
    """Ombrage lisse à angle limite : garde les arêtes vives des sillons."""
    _activer(obj)
    if hasattr(bpy.ops.object, "shade_auto_smooth"):
        bpy.ops.object.shade_auto_smooth(angle=math.radians(angle_deg))
    else:
        bpy.ops.object.shade_smooth()


def eclairage(cible):
    """Éclairage trois points, chaque soleil VISÉ sur le sujet.

    ⚠️ LE PIÈGE, et il était déjà dans la version d'origine : une lampe SUN
    est DIRECTIONNELLE — sa position n'a aucun effet, seule sa ROTATION compte.
    Trois soleils créés à la rotation par défaut pointent donc tous droit vers
    le bas (−Z), quelles que soient les coordonnées données à `light_add`.
    Résultat : la voûte crânienne projetait une ombre dure en travers de la
    face et masquait les orbites — un défaut d'ÉCLAIRAGE qu'on lisait comme un
    défaut de géométrie.

    On accroche donc une contrainte TRACK_TO à chaque soleil : l'axe −Z (la
    direction d'émission) est dirigé vers la cible, ce qui rend enfin la
    position significative.
    """
    for nom, position, energie, couleur in (
        ("Cle", (3.2, 5.2, 2.6), 4.5, (1.00, 0.96, 0.90)),
        ("Rempli", (-4.6, 3.2, 0.6), 1.1, (0.70, 0.79, 1.00)),
        ("Contre", (-1.8, -4.6, 3.0), 1.8, (1.00, 0.86, 0.70)),
    ):
        bpy.ops.object.light_add(type="SUN", location=position)
        lampe = bpy.context.active_object
        lampe.name = f"Lumiere_{nom}"
        lampe.data.energy = energie
        lampe.data.color = couleur
        lampe.data.angle = math.radians(9)  # pénombre douce
        contrainte = lampe.constraints.new(type="TRACK_TO")
        contrainte.target = cible
        contrainte.track_axis = "TRACK_NEGATIVE_Z"
        contrainte.up_axis = "UP_Y"


def cible_visee():
    """Point de mire partagé par la caméra ET les trois soleils."""
    bpy.ops.object.empty_add(location=(0.0, 0.18, -0.06))
    cible = bpy.context.active_object
    cible.name = "Cible_Visee"
    return cible


def camera(cible):
    """Trois-quarts avant.

    ⚠️ Deux erreurs déjà commises ici, toutes deux invisibles au code de sortie :
      - une caméra en −Y ne montre que l'occiput (la face regarde +Y) ;
      - en 1920×1080 le capteur s'ajuste à l'HORIZONTALE (36 mm), donc le champ
        VERTICAL est le facteur limitant. Au 68 mm à 3,7 unités il ne couvrait
        que 1,3 unité pour un crâne de ~2,1 de haut, et le sujet sortait du
        cadre. 55 mm à ~4,9 unités couvre 2,3 — calculé, pas estimé.
    """
    bpy.ops.object.camera_add(location=(3.55, 4.95, 1.45))
    cam = bpy.context.active_object
    cam.name = "Camera_Crane"
    cam.data.lens = 52
    contrainte = cam.constraints.new(type="TRACK_TO")
    contrainte.target = cible
    contrainte.track_axis = "TRACK_NEGATIVE_Z"
    contrainte.up_axis = "UP_Y"

    # La caméra active appartient à la SCÈNE. `view_layer.camera` n'existe pas
    # (AttributeError, vécu le 2026-08-05 sur Blender 5.1.2) : le view layer ne
    # porte qu'une surcharge facultative, et seulement si la scène en a déjà une.
    bpy.context.scene.camera = cam
    return cam


def reglages_rendu():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 192
    scene.cycles.use_denoising = True
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.film_transparent = False

    # Le transform AgX par défaut est volontairement plat ; sans ce Look
    # les cavités orbitaires et les sillons dentaires se noient.
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = -0.35

    monde = bpy.data.worlds[0] if bpy.data.worlds else bpy.data.worlds.new("Fond")
    scene.world = monde
    if monde.node_tree is None:
        monde.use_nodes = True
    fond = monde.node_tree.nodes.get("Background")
    if fond is not None:
        fond.inputs["Color"].default_value = (0.042, 0.047, 0.058, 1.0)


# ====== GARDES ======


def verifier_traversees(crane, mandibule):
    """Un outil qui ne traverse pas la surface ne creuse RIEN, en silence.

    C'est le mode de panne d'origine, et il rendait exit 0. On le refuse ici
    plutôt que de le découvrir à l'œil : chaque contrôle nomme le trait et la
    marge mesurée, en unités Blender.
    """
    echecs = []

    # Orbites : la base du cône doit dépasser la surface faciale.
    surface = _surface_avant(ORBITE_X, ORBITE_Z, FACE_C, FACE_A)
    base = ORBITE_Y + ORBITE_PROF / 2
    if surface is None or base <= surface:
        echecs.append(
            f"orbite : base du cône en y={base:.3f} n'atteint pas la surface "
            f"y={surface if surface is None else round(surface, 3)}"
        )

    # Nez : le cône doit traverser APRÈS son étirement en Y.
    surface = _surface_avant(0.0, NEZ_Z, FACE_C, FACE_A)
    avant = NEZ_Y + NEZ_RAYON * NEZ_ETIREMENT_Y
    if surface is None or avant <= surface:
        echecs.append(
            f"nez : cône en y={avant:.3f} n'atteint pas la surface y={surface}"
        )

    # Dents : l'arcade doit DÉPASSER sous le maxillaire, sinon elle est noyée.
    d2 = _norme_ellipsoide(
        (0.0, ARCADE_SUP_Y + ARCADE_SUP_R, ARCADE_SUP_Z), FACE_C, FACE_A
    )
    if d2 <= 1.0:
        echecs.append(f"dents : arcade noyée dans le maxillaire (d²={d2:.3f} ≤ 1)")

    # Branches mandibulaires : elles doivent naître SUR l'arc, pas à côté.
    if BRANCHE_X > MANDIBULE_R:
        echecs.append(
            f"mandibule : branches en x=±{BRANCHE_X} hors de l'arc "
            f"(rayon {MANDIBULE_R}) — elles flotteraient dans le vide"
        )

    # Pommettes : le renflement doit AFFLEURER — assez pour se voir, pas
    # assez pour se détacher. ⚠️ Tester le FLANC, pas le centre : la version
    # au cylindre passait ce contrôle sur son axe (d²=0,79) pendant que son
    # flanc en x+rayon sortait de la masse (d²=1,03) et formait un éperon.
    flanc = _norme_ellipsoide(
        (ZYGO_X + ZYGO_A[0], ZYGO_Y, ZYGO_Z), FACE_C, FACE_A
    )
    if not 0.95 <= flanc <= 1.15:
        echecs.append(
            f"pommette : flanc à d²={flanc:.3f} — hors de [0,95 ; 1,15], "
            "soit noyée dans la face, soit détachée en éperon"
        )

    # Trace des creusements dans le maillage.
    for nom, obj, plancher in (("crâne", crane, 5000), ("mandibule", mandibule, 1200)):
        if len(obj.data.polygons) < plancher:
            echecs.append(
                f"{nom} : {len(obj.data.polygons)} polygones (< {plancher}) — "
                "les booléens n'ont probablement rien coupé"
            )

    return echecs


def main():
    purger_scene()

    crane = construire_crane()
    mandibule = construire_mandibule()

    crane.data.materials.append(materiau_os("Os_Crane", (0.74, 0.70, 0.62, 1.0), 0.62))
    mandibule.data.materials.append(
        materiau_os("Os_Mandibule", (0.70, 0.66, 0.58, 1.0), 0.64)
    )

    lisser(crane)
    lisser(mandibule)

    cible = cible_visee()
    eclairage(cible)
    camera(cible)
    reglages_rendu()

    echecs = verifier_traversees(crane, mandibule)
    if echecs:
        print("\n❌ GARDE : un outil de coupe ne traverse plus la surface.")
        for e in echecs:
            print(f"   - {e}")
        # Sortie non nulle : sans ça Blender rend 0 et le crâne redevient un
        # ovoïde sans que rien ne rougisse (incident du 2026-08-05).
        sys.exit(1)

    print(f"✅ Crâne : {len(crane.data.polygons)} polygones")
    print(f"✅ Mandibule : {len(mandibule.data.polygons)} polygones")
    print("   Orbites, ouverture nasale et sillons dentaires CREUSÉS (booléens).")


if __name__ == "__main__":
    main()
