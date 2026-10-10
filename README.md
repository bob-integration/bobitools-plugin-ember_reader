# Ember+ Reader — plugin Bobi.Tools

Lecteur **Ember+** générique pour [Bobi.Tools](https://github.com/bob-integration/bobitools) :
on donne l'adresse d'un provider (contrôleur broadcast, équipement, ou le service Ember+ de
Bobi.Tools lui-même) et on parcourt son arbre, sans outil tiers.

## Ce que fait l'outil

- Se connecte à un provider (`hôte` : `port`) et lit l'arbre **à la demande** : la racine
  d'abord, chaque branche au moment où on la déplie.
- **Relevé complet** d'un bouton, avec un avertissement explicite si le relevé est écourté
  (délai, profondeur ou nombre de nœuds atteint).
- Affiche nœuds et paramètres, valeurs, énumérations résolues, accès lecture seule.
- **Écriture** d'un paramètre inscriptible, après saisie et confirmation ; le résultat est
  vérifié par relecture.
- Mémorise les cibles récentes.

## À savoir

- Les **matrices** ne sont pas décodées : seuls les nœuds et paramètres apparaissent.
- Un provider volumineux peut mettre plusieurs secondes à pousser son arbre : augmenter le
  délai plutôt que conclure à un arbre vide.

## Prérequis

- Aucun : l'outil tourne dans Bobi.Tools. Le client Ember+ (S101 + BER) est autonome et ne
  dépend pas du service [Ember+](https://github.com/bob-integration/bobitools-service-emberplus).

## Installation

Dans Bobi.Tools : **Réglages → Outils → Catalogue**, bouton « Installer ». Ou, sur une machine
neuve, en une ligne :

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bob-integration/bobitools/main/get.sh) --outils ember_reader
```

L'aide complète est dans [`help.md`](help.md), affichée dans Bobi.Tools (menu « ? » → Aide).

## Sécurité

Ember+ n'a pas d'authentification : les connexions partent du serveur Bobi.Tools. L'écriture
demande la permission « Écrire un paramètre Ember+ », et chaque relevé ou écriture est inscrit
au journal d'audit.

## In English

Generic **Ember+** browser for Bobi.Tools: connects to any provider and reads its tree on
demand (or in one full crawl), showing nodes, parameters, values and resolved enums.
Writable parameters can be set after confirmation, verified by read-back. Matrices are not
decoded. Runs in-process with a self-contained S101/BER client.

## Licence

GPL-3.0-or-later — © 2026 BOBI SAS. Voir [LICENSE](LICENSE).
