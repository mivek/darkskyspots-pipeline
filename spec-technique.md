# Spec technique d'implémentation — App « ciel nocturne »

*Document destiné à un agent de code (OpenCode). À lire avec `recap-app-ciel-nocturne.md` (le « pourquoi ») et `spec-design.md` (l'apparence, à respecter strictement).*

> **Règle pour l'agent :** les valeurs marquées `[CONFIG]` sont des constantes ajustables à centraliser dans un seul fichier `config/constants.ts`. Ne pas les disperser dans le code. Ne pas réinterpréter les choix de design : se référer à `spec-design.md` pour tout ce qui est visuel.

---

## 1. Stack imposée

| Couche | Choix | Raison |
|---|---|---|
| Framework | **React Native + Expo** (managed workflow) | Mobile iOS/Android + chemin web naturel, écosystème JS mature. |
| Langage | **TypeScript** (strict) | Sûreté de typage, meilleure génération de code. |
| Carte | **MapLibre GL** (`@maplibre/maplibre-react-native`) | Open-source, **sans quota ni coût**. NE PAS utiliser Mapbox ni Google Maps (payants à l'usage). |
| Fond de carte | Style raster/vector libre (ex. tuiles OSM ou un style sombre libre) | Gratuit. Voir §7. |
| Stockage local | **expo-file-system** (fichiers de spots) + **AsyncStorage** (préférences, métadonnées de cache) | Persistant, simple. |
| Géolocalisation | **expo-location** | Permission « while in use » uniquement. |
| Calcul astronomique | **suncalc** (npm) | Lune + position du Soleil, 100 % local, aucun réseau. |
| Météo | **Open-Meteo** (REST, sans clé) | Couverture nuageuse horaire. |
| État | Hooks React + Context (ou Zustand si besoin) | Pas de Redux, surdimensionné ici. |

> **Cible web (phase 2) :** privilégier des libs compatibles React Native Web. MapLibre a un équivalent web direct (`maplibre-gl`). Ne pas introduire de dépendance qui bloquerait le portage web.

---

## 2. Structure du projet

```
/src
  /config
    constants.ts        // toutes les valeurs [CONFIG]
    theme.ts            // tokens de design (voir spec-design.md)
  /core                 // logique pure, testable SANS UI ni réseau
    score.ts            // calcul du score (la pièce maîtresse)
    astro.ts            // wrappers suncalc : nadir, altitude soleil, lune
    spots.ts            // chargement, cache, recherche par rayon
    tiles.ts            // calcul de la tuile + voisines depuis lat/lon
  /data
    weather.ts          // client Open-Meteo + cache daté
    spotsRepo.ts        // téléchargement depuis le repo GitHub + versionnage
  /ui
    /screens            // un fichier par écran
    /components         // ScoreRing, SpotCard, etc.
    /theme              // application des tokens
  /hooks
/assets
  spots-seed.json       // (optionnel) jeu de spots minimal embarqué — voir récap §8
```

Principe : **`/core` ne dépend ni de l'UI ni du réseau.** On doit pouvoir tester `score.ts` et `astro.ts` en isolation. Toute la valeur de l'app est là.

---

## 3. Constantes `[CONFIG]`

```ts
// config/constants.ts
export const CONFIG = {
  // Score
  WEATHER_EXPONENT: 2,            // facteur_météo = (1 - couverture)^2
  TWILIGHT_FULL_DARK_DEG: -18,    // soleil sous -18° = nuit astronomique complète
  TWILIGHT_USABLE_DEG: -12,       // entre -18 et -12 : dégradé ; au-dessus : s'effondre
  BORTLE_MIN: 1,                  // pour normaliser darkness (1 = noir parfait)
  BORTLE_MAX: 9,                  // 9 = centre-ville

  // Recherche de spots
  SEARCH_RADIUS_KM: 75,           // rayon par défaut (50–100 validé)
  SEARCH_RADIUS_MAX_KM: 150,      // extensible
  MAX_SPOTS_DISPLAYED: 20,        // densité affichée à l'écran
  MIN_SPOTS_GUARANTEED: 4,        // garantie de couverture (pré-calcul)

  // Tuiles
  TILE_SIZE_DEG: 1.0,             // ~110 km de côté
  // L'app charge la tuile contenant l'utilisateur + ses 8 voisines.

  // Météo / cache
  WEATHER_FRESH_MAX_H: 3,         // au-delà : afficher "Météo d'il y a Xh"
  WEATHER_STALE_MAX_H: 12,        // au-delà : info grisée, score dégradé
  SPOTS_VERSION_CHECK_MAX_PER_DAY: 1,
  OBSERVATION_HOUR_MODE: 'nadir', // score calculé au milieu de la nuit astronomique
} as const;
```

---

## 4. Logique de score (`core/score.ts`)

Implémenter exactement la formule du récap §2 :

```
score01 = darkness × weatherFactor × moonFactor × darknessFactor
scoreOutOf10 = Math.round(score01 * 10)
```

Détail des facteurs (tous ∈ [0,1]) :

- **darkness** : fourni par le spot (`spot.darkness`). Si seul `bortle` est dispo : `darkness = (BORTLE_MAX - bortle) / (BORTLE_MAX - BORTLE_MIN)`.
- **weatherFactor** : `Math.pow(1 - cloudCover01, WEATHER_EXPONENT)`. `cloudCover01` = couverture nuageuse 0–1 à l'heure d'observation.
- **moonFactor** : via suncalc. Si la lune est sous l'horizon à l'heure d'observation → `1`. Sinon `1 - illuminationFraction` (lune pleine et haute = proche de 0). Pondérer par l'altitude lunaire si on veut affiner (lune basse = moins gênante).
- **darknessFactor** : via suncalc, altitude du Soleil `h` (deg) à l'heure d'observation :
  - `h <= -18` → `1`
  - `-18 < h < -12` → interpolation linéaire de `1` vers `~0.4`
  - `h >= -12` → décroît vite vers `0` (ciel pas assez sombre / nuit blanche)

**Heure d'observation** : `SunCalc.getTimes(date, lat, lon).nadir` (milieu de la nuit astronomique). Si aucune nuit astronomique (nadir où le soleil reste au-dessus de -18°/-12°), `darknessFactor` tranche bas → score bas → message « Nuit blanche ».

**Explication (maillon faible)** : retourner aussi le facteur limitant pour générer la phrase. Fonction `explainScore(factors)` → renvoie une clé (`'clouds' | 'moon' | 'twilight' | 'ideal'`) que l'UI mappe vers un texte (voir spec-design pour les libellés exacts).

**Mode dégradé (hors-ligne, météo absente)** : si `cloudCover01` indisponible, calculer un `partialScore` basé sur `darkness × moonFactor × darknessFactor` et marquer `weatherMissing: true`. L'UI affiche « météo à confirmer ».

Sortie suggérée :
```ts
interface ScoreResult {
  score: number;            // 0–10 arrondi
  raw: number;              // 0–1 non arrondi
  factors: { darkness: number; weather: number | null; moon: number; twilight: number };
  limiting: 'clouds' | 'moon' | 'twilight' | 'ideal' | 'none';
  weatherMissing: boolean;
}
```

Tests unitaires attendus : reproduire les 3 exemples de validation du récap §2 (Beille 8 % nuages ≈ 8 ; 95 % nuages ≈ 0 ; pleine lune dégagé ≈ 2) + un cas nuit blanche (Tromsø juin → 0).

---

## 5. Spots : chargement, cache, recherche

### Schéma du fichier (par tuile)
Conforme au récap §5 :
```json
{
  "version": "2025.1",
  "source": "VIIRS 2025 (NASA, CC0)",
  "generated": "2026-02-15",
  "tile": "N42E001",
  "spots": [
    { "id": "42.7283_1.6492", "lat": 42.7283, "lon": 1.6492,
      "darkness": 0.91, "bortle": 2, "name": "Plateau de Beille",
      "near": "Tarascon-sur-Ariège", "altitude": 1800 }
  ]
}
```

### Nommage des tuiles
`tiles.ts` : depuis `(lat, lon)` et `TILE_SIZE_DEG`, calculer l'identifiant de tuile (ex. `N42E001` = coin SW à lat 42, lon 1). Fournir `tileId(lat, lon)` et `neighbors(tileId)` → 8 voisines.

### URL du repo
`spotsRepo.ts` construit l'URL brute GitHub :
`https://raw.githubusercontent.com/<user>/<repo>/<branch>/spots/<tileId>.json`
`[CONFIG]` : `SPOTS_BASE_URL`. (Repo public en lecture ; voir note sécurité du récap §4 — un token ne protégerait pas réellement.)

### Cache
- Télécharger tuile + voisines, stocker via `expo-file-system` (persistant).
- Métadonnées (version, date de téléchargement par tuile) dans AsyncStorage.
- Au lancement : servir depuis le cache immédiatement ; vérifier une nouvelle version **au plus 1×/jour** (`version` du fichier) et re-télécharger seulement si supérieure.

### Recherche par rayon (`spots.ts`)
- Agréger les spots des tuiles chargées, filtrer par distance haversine ≤ `SEARCH_RADIUS_KM`, trier par score du soir (calculé à la volée), tronquer à `MAX_SPOTS_DISPLAYED` pour l'affichage.
- Tout en mémoire, hors-ligne-compatible.

### Repli hors-ligne (récap §8)
1. cache → 2. `spots-seed.json` embarqué (si présent) → 3. écran « connectez-vous une fois ».
Météo : fraîche → datée → partielle (score sans météo).

---

## 6. Météo (`data/weather.ts`)

- Endpoint Open-Meteo, paramètre `cloudcover` horaire, pour `(lat, lon)` du spot (ou de la zone).
- Récupérer la valeur à l'heure d'observation (nadir).
- **Cacher avec horodatage.** Exposer l'âge de la donnée à l'UI pour les libellés « Météo d'il y a Xh ».
- Pré-charger la météo des spots proches quand le réseau est là (récap §8 pré-chargement intelligent).

---

## 7. Carte (MapLibre)

- Style **sombre** (voir spec-design pour les couleurs ; le fond ne doit pas concurrencer les pastilles de score).
- Fond de carte : tuiles libres (OSM raster sombre, ou style vector libre type « dark matter » de CARTO si licence compatible usage gratuit — **à vérifier**, sinon raster OSM).
- **Calque pollution lumineuse** : superposer une version tuilée/raster de la donnée VIIRS basse résolution en overlay semi-transparent (généré au pré-calcul, hébergé en raster). Optionnel au tout premier MVP si lourd — la valeur principale vient des pastilles de spots.
- **Marqueurs = pastilles de score** (composant custom, voir spec-design) : couleur selon score, chiffre au centre. Pas de marqueur générique.
- Interactions : tap pastille → fiche spot (bottom sheet). Recherche de lieu → recentre + charge tuiles.

---

## 8. Permissions & onboarding (récap §7)

- `expo-location` : demander **« while in use »** au tap sur « Activer ma position » (écran 2 de l'onboarding), **pas** au démarrage.
- Écran pré-permission custom AVANT la pop-up système.
- Refus → recherche manuelle (toujours dispo) ; pré-charger via position réseau approximative si possible.
- Fin d'onboarding : pré-charger la tuile courante + voisines tant que le réseau est là, puis message adapté (succès / « connectez-vous une fois »).
- Rattrapage : bouton ouvrant les réglages système (`Linking.openSettings()`).
- Pas de permission notifications (hors MVP).

---

## 9. Ordre d'implémentation (jalons)

1. **Setup** : Expo + TypeScript strict + structure de dossiers + `config/constants.ts` + `theme.ts`.
2. **`/core` pur, testé** : `astro.ts` (suncalc) → `score.ts` + tests reproduisant les exemples de validation. **Aucune UI à ce stade.** Jalon vérifiable = tests verts.
3. **Spots** : `tiles.ts`, `spots.ts`, `spotsRepo.ts`, cache. Tester recherche par rayon sur un JSON local.
4. **Météo** : `weather.ts` + cache daté + mode dégradé.
5. **UI carte** : MapLibre + style sombre + pastilles de score (respecter spec-design).
6. **Fiche spot** (bottom sheet) + **écran prévisions** (5–7 nuits).
7. **Onboarding** + permissions + repli hors-ligne + pré-chargement.
8. **Mode rouge** (vision nocturne) : thème alternatif appliqué globalement (tokens du mode rouge dans spec-design).
9. Polish, edge cases hors-ligne, vérif coûts (aucune lib payante introduite).

> À chaque jalon, ne pas avancer tant que le précédent n'est pas fonctionnel et (pour 1–4) testé.

---

## 10. Garde-fous (à ne pas violer)

- **Aucune dépendance payante à l'usage** (cartes, météo, hébergement). Si une lib a un quota, le signaler plutôt que l'intégrer silencieusement.
- **`/core` sans réseau ni UI.**
- **Respecter `spec-design.md` au pixel** : couleurs hex, tailles, structure d'écran. Ne pas inventer de style.
- **Pas de suivi de position en arrière-plan.**
- Tout nombre affiché est arrondi proprement (pas de `0.300000004`).
- Centraliser les constantes ; ne pas hardcoder de seuils dans les composants.
