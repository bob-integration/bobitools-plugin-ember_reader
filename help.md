# Ember+ Reader

Inspecteur d'arbre **Ember+** générique, en lecture seule.

## Usage
Saisir l'adresse d'un provider Ember+ (`host` : `port`) puis **Lire l'arbre**. L'outil
se connecte, fait un `GetDirectory` récursif et affiche tout l'arbre : nœuds et
paramètres repliables, valeurs, énumérations résolues, drapeau lecture seule (RO).
Un filtre plein-texte masque tout ce qui ne correspond pas (les ancêtres sont gardés
pour le contexte). Les cibles récentes sont mémorisées.

Exemple : un **Ross Newt** répond sur le port **9095**.

## À quoi ça sert
- **Debug** : inspecter n'importe quel provider Ember+ du réseau (y compris le provider
  Bobi.Tools lui-même) sans outil tiers.
- **Base de pilotage** : le client Ember+ (`ember_client.py`) est autonome (S101 + BER,
  sans dépendance au service `emberplus`) et sert de brique pour piloter des équipements
  Ember+ natifs.

## Limites
- Lecture seule (aucun `SetValue`).
- Les matrices ne sont pas décodées (nœuds/paramètres uniquement).
