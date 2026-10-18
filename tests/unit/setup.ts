import 'fake-indexeddb/auto';
import { beforeEach } from 'vitest';
import { clearAll } from '../../src/storage/database';
beforeEach(async () => {
  await clearAll();
});
