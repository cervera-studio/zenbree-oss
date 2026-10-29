import { createContext } from 'react';

/**
 * False while the chat's list panel slides in. The table waits for it: inserting a page of rows
 * costs one style, layout and commit task of about 18 ms, which would stall the slide.
 */
export const PanelSettled = createContext(true);
