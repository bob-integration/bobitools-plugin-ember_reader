# Ember+ Reader

Inspecteur d'arbre **Ember+** générique : on parcourt l'arbre de n'importe quel provider, et
l'on peut écrire un paramètre inscriptible.

## Usage
Saisir l'adresse d'un provider Ember+ (`hôte` : `port`) puis **Lire l'arbre**. L'outil lit
**à la demande** : la racine d'abord, puis chaque branche au moment où on la déplie. Un
provider volumineux s'ouvre ainsi tout de suite.

**Tout relever** parcourt l'arbre entier d'un coup. C'est plus lent sur un gros provider, mais
c'est le seul mode où le **filtre** plein-texte voit l'ensemble (en lecture à la demande, il ne
filtre que ce qui a déjà été déplié). Si le relevé s'arrête avant la fin (délai, profondeur
ou nombre de nœuds atteint), un avertissement le dit : un arbre écourté n'est pas un arbre
complet.

On voit les nœuds et paramètres repliables, les valeurs, les énumérations résolues et le
drapeau lecture seule (RO). Les cibles récentes sont mémorisées.

## Écrire un paramètre
Un paramètre inscriptible porte un bouton **RW ✎** : saisie, puis confirmation, car on écrit
réellement sur l'équipement. Le type est déduit de la valeur relevée (texte, entier, booléen,
réel), pour ne pas écrire une chaîne à la place d'un entier. Un provider Ember+ n'acquitte pas
une écriture : le résultat affiché vient d'une **relecture** du paramètre.

L'écriture demande la permission « Écrire un paramètre Ember+ » ; relevés et écritures sont
inscrits au journal d'audit.

## À quoi ça sert
- **Debug** : inspecter n'importe quel provider Ember+ du réseau (y compris le provider
  Bobi.Tools lui-même) sans outil tiers — et vérifier ce qu'un contrôleur voit réellement.
- **Base de pilotage** : le client Ember+ (`ember_client.py`) est autonome (S101 + BER,
  sans dépendance au service `emberplus`) et sert de brique pour piloter des équipements
  Ember+ natifs.

## Limites
- Les matrices ne sont pas décodées (nœuds et paramètres uniquement).
- Un provider lent peut mettre plusieurs secondes à pousser une branche : augmenter le délai
  plutôt que conclure à un arbre vide.
