/**
 * Prague Lions Ranking - Main Scripts
 */

/**
 * Player Search & Selection Component
 * Used on the match creation form for team selection
 */
function setupPlayerSearch(config) {
    const {
        searchInputId,
        resultsId,
        playersContainerId,
        hiddenInputId,
        playersArray,
        otherPlayersArray
    } = config;

    const searchInput = document.getElementById(searchInputId);
    const resultsDiv = document.getElementById(resultsId);
    const playersContainer = document.getElementById(playersContainerId);
    const hiddenInput = document.getElementById(hiddenInputId);

    if (!searchInput || !resultsDiv || !playersContainer || !hiddenInput) {
        console.error('Player search: Missing required DOM elements');
        return;
    }

    let debounceTimer;

    async function fetchUsers(query) {
        const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`);
        return response.json();
    }

    function showResults(users) {
        resultsDiv.innerHTML = '';
        const selectedIds = [...playersArray.map(p => p.id), ...otherPlayersArray.map(p => p.id)];
        const availableUsers = users.filter(u => !selectedIds.includes(u.id));

        if (availableUsers.length === 0) {
            const div = document.createElement('div');
            div.className = 'search-result-item search-result-item--empty';
            div.textContent = 'No players available';
            resultsDiv.appendChild(div);
        } else {
            availableUsers.forEach(user => {
                const div = document.createElement('div');
                div.className = 'search-result-item';
                div.textContent = user.username;
                div.addEventListener('click', () => {
                    addPlayer(user);
                    resultsDiv.classList.remove('show');
                    searchInput.value = '';
                });
                resultsDiv.appendChild(div);
            });
        }
        resultsDiv.classList.add('show');
    }

    function addPlayer(user) {
        playersArray.push(user);
        updateDisplay();
    }

    function removePlayer(userId) {
        const index = playersArray.findIndex(p => p.id === userId);
        if (index > -1) {
            playersArray.splice(index, 1);
            updateDisplay();
        }
    }

    function updateDisplay() {
        hiddenInput.value = JSON.stringify(playersArray.map(p => p.id));

        if (playersArray.length === 0) {
            playersContainer.innerHTML = '<span class="no-players">No players selected</span>';
        } else {
            playersContainer.innerHTML = playersArray.map(player => `
                <span class="player-tag">
                    ${player.username}
                    <button type="button" class="remove-btn" data-id="${player.id}">&times;</button>
                </span>
            `).join('');

            playersContainer.querySelectorAll('.remove-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    removePlayer(parseInt(btn.dataset.id));
                });
            });
        }
    }

    // Event listeners
    searchInput.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        const query = this.value.trim();

        debounceTimer = setTimeout(async () => {
            const users = await fetchUsers(query);
            showResults(users);
        }, 200);
    });

    searchInput.addEventListener('focus', async function() {
        if (this.value.trim() === '') {
            const users = await fetchUsers('');
            showResults(users);
        }
    });

    document.addEventListener('click', function(e) {
        if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {
            resultsDiv.classList.remove('show');
        }
    });
}

/**
 * Match Form Initialization
 * Sets up player search for both teams and form validation
 */
function initMatchForm() {
    const matchForm = document.getElementById('matchForm');
    if (!matchForm) return;

    const teamAPlayers = [];
    const teamBPlayers = [];

    setupPlayerSearch({
        searchInputId: 'searchTeamA',
        resultsId: 'resultsTeamA',
        playersContainerId: 'playersTeamA',
        hiddenInputId: 'team_a_players',
        playersArray: teamAPlayers,
        otherPlayersArray: teamBPlayers
    });

    setupPlayerSearch({
        searchInputId: 'searchTeamB',
        resultsId: 'resultsTeamB',
        playersContainerId: 'playersTeamB',
        hiddenInputId: 'team_b_players',
        playersArray: teamBPlayers,
        otherPlayersArray: teamAPlayers
    });

    matchForm.addEventListener('submit', function(e) {
        if (teamAPlayers.length === 0 || teamBPlayers.length === 0) {
            e.preventDefault();
            alert('Both teams must have at least one player.');
        }
    });
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', function() {
    initMatchForm();
});
