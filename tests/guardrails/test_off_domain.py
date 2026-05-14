"""Unit tests for the off-domain prompt classifier."""

from __future__ import annotations

import pytest

from toolforge.guardrails.off_domain import classify_off_domain


@pytest.mark.unit
class TestOffDomainDetected:
    def test_write_poem(self):
        assert classify_off_domain("write me a poem") == "poem_story_song"

    def test_write_a_poem_no_me(self):
        assert classify_off_domain("write a poem about autumn") == "poem_story_song"

    def test_write_story(self):
        assert classify_off_domain("write me a story") == "poem_story_song"

    def test_write_song(self):
        assert classify_off_domain("write a song for my birthday") == "poem_story_song"

    def test_write_essay(self):
        assert classify_off_domain("write an essay on climate change") == "poem_story_song"

    def test_tell_me_a_joke(self):
        assert classify_off_domain("tell me a joke") == "joke_trivia"

    def test_tell_me_a_joke_with_qualifier(self):
        assert classify_off_domain("tell me a joke about cats") == "joke_trivia"

    def test_whats_the_weather(self):
        assert classify_off_domain("what's the weather today?") == "weather"

    def test_what_is_the_weather(self):
        assert classify_off_domain("what is the weather in Paris") == "weather"

    def test_translate(self):
        assert classify_off_domain("translate this to French") == "translate"

    def test_translate_into(self):
        assert classify_off_domain("translate the following into Spanish") == "translate"

    def test_horoscope(self):
        assert classify_off_domain("what is my horoscope") == "horoscope_recipe"

    def test_give_me_a_recipe(self):
        assert classify_off_domain("give me a recipe for pasta") == "horoscope_recipe"


@pytest.mark.unit
class TestOffDomainNotDetected:
    @pytest.mark.parametrize("text", [
        # Operational prompts that contain off-domain words as content
        "read the poem.txt file",
        "open the issue called Story Time",
        "commit the file named jokes.py",
        "translate the error message in the log file",
        "what is the weather module doing in this repo",
        # Clearly operational prompts
        "read /tmp/toolforge-demo/hello.txt",
        "create a pull request for the feature branch",
        "list files in the directory",
        "search GitHub for issues mentioning rate limit",
        "send a slack message to the engineering channel",
        "build and deploy the service",
        "show the git diff for the last commit",
        "refactor the authentication module",
        "",
        "hello",
    ])
    def test_benign_prompt_not_detected(self, text: str):
        assert classify_off_domain(text) is None
