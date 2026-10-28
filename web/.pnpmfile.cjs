// openapi-typescript drives the TypeScript 5 compiler API, which TypeScript 7 (native) does not export.
// Give it its own TypeScript 5 instead of the project's 7.
// Owner: whoever bumps openapi-typescript or typescript (issue #601). Remove this file and the
// `.pnpmfile.cjs` COPY in Dockerfile when a release of openapi-typescript lists TypeScript 7 in its
// peerDependencies (7.13.0 lists ^5.x). Upstream: https://github.com/openapi-ts/openapi-typescript/issues/2841
// Check with: pnpm view openapi-typescript peerDependencies
// The effect rules run in Oxlint (jsPlugins), which needs no ESLint: drop the plugin's ESLint peer,
// or pnpm installs ESLint for nothing.
function readPackage(pkg) {
  if (pkg.name === 'openapi-typescript') {
    delete pkg.peerDependencies.typescript;
    pkg.dependencies = { ...pkg.dependencies, typescript: 'npm:typescript@5.9.3' };
  }
  if (pkg.name === 'eslint-plugin-react-you-might-not-need-an-effect')
    delete pkg.peerDependencies.eslint;
  return pkg;
}

module.exports = { hooks: { readPackage } };
