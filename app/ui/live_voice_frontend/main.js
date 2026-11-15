import { Streamlit } from 'streamlit-component-lib';
import { Conversation } from '@elevenlabs/client';
import { setupLiveVoice } from './controller.js';
setupLiveVoice({Streamlit,Conversation,navigator,crypto,document,window});
