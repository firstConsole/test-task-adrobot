import js from '@eslint/js'
import boundaries from 'eslint-plugin-boundaries'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'
import tseslint from 'typescript-eslint'

/**
 * Feature-Sliced, as a graph: a layer may import the layers below it and nothing else, and
 * a slice may never import a sibling slice. That last half is what forces `features/` to
 * hold only the mutations — the reading and the rendering of a business object have to live
 * in `entities/`, which is the one layer both screens can reach.
 */
const CAN_IMPORT = {
  app: ['pages', 'widgets', 'features', 'entities', 'shared'],
  pages: ['widgets', 'features', 'entities', 'shared'],
  widgets: ['features', 'entities', 'shared'],
  features: ['entities', 'shared'],
  entities: ['shared'],
  shared: ['shared'],
}

/** The layers reached through a slice `index.ts`; `shared` is segments and has no barrels. */
const BARRELLED = ['pages', 'widgets', 'features', 'entities']

export default tseslint.config(
  { ignores: ['dist/'] },

  {
    files: ['eslint.config.js'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },

  {
    files: ['vite.config.ts'],
    extends: [js.configs.recommended, tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      globals: globals.node,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
  },

  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      reactHooks.configs.flat['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    plugins: { boundaries },
    settings: {
      // Without a TypeScript-aware resolver `@/features/x` reads as the scoped npm package
      // `@/features`, every import is classified external, and the graph below matches
      // nothing while reporting success.
      'import/resolver': { typescript: { project: './tsconfig.app.json' } },
      'boundaries/include': ['src/**/*'],
      'boundaries/elements': [
        { type: 'app', pattern: 'src/app', partialMatch: false },
        { type: 'pages', pattern: 'src/pages/*', partialMatch: false, capture: ['slice'] },
        { type: 'widgets', pattern: 'src/widgets/*', partialMatch: false, capture: ['slice'] },
        { type: 'features', pattern: 'src/features/*', partialMatch: false, capture: ['slice'] },
        { type: 'entities', pattern: 'src/entities/*', partialMatch: false, capture: ['slice'] },
        { type: 'shared', pattern: 'src/shared/*', partialMatch: false, capture: ['segment'] },
      ],
    },
    rules: {
      'boundaries/dependencies': [
        'error',
        {
          default: 'disallow',
          message: '{{from.types}} may not import {{to.types}}',
          policies: [
            ...Object.entries(CAN_IMPORT).map(([layer, allowed]) => ({
              from: { element: { type: layer } },
              allow: { to: { element: { types: { anyOf: allowed } } } },
            })),
            // Last, so it overrides the allowances above: a slice is entered through its
            // own index.ts and never file by file.
            {
              disallow: {
                to: {
                  element: { types: { anyOf: BARRELLED } },
                  file: { path: '!**/index.ts' },
                },
              },
              message: 'reach {{dependency.source}} through the slice index.ts',
            },
          ],
        },
      ],
    },
  },

  {
    // Vendored from the shadcn registry. Left as the registry ships it, so that
    // `shadcn add --diff` stays readable when a component is updated.
    files: ['src/shared/ui/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
)
