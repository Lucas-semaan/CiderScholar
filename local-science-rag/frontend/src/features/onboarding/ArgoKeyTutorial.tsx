export function ArgoKeyTutorial() {
  return (
    <details className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
      <summary className="cursor-pointer font-semibold text-slate-800">
        Comment obtenir ma clé ARGO ?
      </summary>
      <ol className="mt-3 list-decimal space-y-2 pl-5 leading-6">
        <li>Connectez le poste au réseau INRAE ou au VPN institutionnel si nécessaire.</li>
        <li>Ouvrez le site ARGO et connectez-vous à votre compte personnel.</li>
        <li>Dans le profil, ouvrez Réglages, puis Compte, puis Clé API.</li>
        <li>Cliquez sur Afficher et copiez la clé complète.</li>
        <li>Revenez dans CiderScholar, collez la clé et lancez sa vérification.</li>
      </ol>
      <p className="mt-3 leading-6">
        Ne transmettez jamais cette clé et ne la placez ni dans un fichier, ni dans SharePoint, ni
        dans une capture d’écran. Supprimez-la avant de céder le poste.
      </p>
      <h3 className="mt-4 font-semibold text-slate-800">Rotation ou cession du poste</h3>
      <ol className="mt-2 list-decimal space-y-2 pl-5 leading-6">
        <li>Créez une nouvelle clé dans ARGO si une rotation est nécessaire.</li>
        <li>Dans Paramètres, remplacez la clé et lancez Tester la connexion.</li>
        <li>Révoquez ensuite l’ancienne clé dans ARGO.</li>
        <li>
          Avant toute cession du poste, utilisez Supprimer dans Paramètres et vérifiez le statut
          Absente.
        </li>
      </ol>
    </details>
  );
}

export function ArgoNetworkNotice() {
  return (
    <p className="rounded-xl border border-cider-200 bg-cider-50 px-4 py-3 text-sm leading-6 text-slate-700">
      Avant de tester la connexion, vérifiez le réseau INRAE ou le VPN si votre accès ARGO l’exige.
      Cet avertissement ne bloque pas le test.
    </p>
  );
}
