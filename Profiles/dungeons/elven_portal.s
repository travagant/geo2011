//Dungeon level 3

id	"Elven_portal";

scene	"Resources\\Hexagons\\Models\\Arena\\arena_back_dungeon\\arena_back_dungeon_02_hexmap.scene";

// party
// unit "id" lvlcap;
// or
// class amount "unit_side" level lvlcap;

party						
{						
	melee	2	"elves_giant"	18-25	25	;
}						
						
party						
{						
	melee		"elves_giant"	30-40	40	;
}						
						
party						
{						
	melee	2	"elves"	5-17	17	;
	melee		"elves_giant"	18-22	22	;
}						
						
party						
{						
	melee	2	"elementals"	7	17	;
	melee		"elves_giant"	18-22	22	;
}						
						
party						
{						
	melee	2	"elves"	5-15	15	;
	melee	2	"elves"	10-22	22	;
}						
						
party						
{						
	melee	2	"elementals"	7	15	;
	melee	2	"elves"	10-22	22	;
}						
						
party						
{						
	melee	2	"elves"	5-17	17	;
	melee		"elves"	5-17	17	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	melee	2	"elves"	5-15	15	;
	melee		"elves"	10-22	22	;
	healer		"elves"	5-13	13	;
}						
						
party						
{						
	melee	2	"elementals"	7	15	;
	melee		"elves"	10-22	22	;
	healer		"elves"	5-13	13	;
}						
						
party						
{						
	melee	2	"elves"	1-9	9	;
	melee		"elves"	5-17	17	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee	2	"elves"	1-9	9	;
	melee		"elves_giant"	18-22	22	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee	2	"elementals"	7	9	;
	melee		"elves_giant"	18-22	22	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee	2	"elves"	5-17	17	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee	2	"elementals"	7	17	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee		"elves_giant"	18-22	22	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	melee		"elves_giant"	30	30	;
	caster	2	"elves"	1-9	9	;
}						
						
party						
{						
	melee		"elves_giant"	18-22	22	;
	caster		"elves"	5-16	16	;
	healer		"elves"	5-22	22	;
}						
						
party						
{						
	melee	2	"elves"	5-17	17	;
	caster		"elves"	5-16	16	;
	healer		"elves"	5-22	22	;
}						
						
party						
{						
	melee	2	"elementals"	7	9	;
	caster	2	"elves"	5-16	16	;
	healer		"elves"	5-22	22	;
}						
						
party						
{						
	melee	2	"elves"	1-9	9	;
	caster		"elves"	13-25	25	;
	caster	2	"elves"	1-9	9	;
}						
						
party						
{						
	melee	2	"elementals"	7	9	;
	caster		"elves"	13-25	25	;
	caster	2	"elves"	1-9	9	;
}						
						
party						
{						
	melee		"elves_giant"	18-22	22	;
	caster	2	"elves"	1-9	9	;
	caster	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster	2	"elves"	5-16	16	;
	caster		"elves"	13-25	25	;
}						
						
party						
{						
	caster	2	"elves"	5-16	16	;
	caster	2	"elves"	5-16	16	;
}						
						
party						
{						
	caster	2	"elves"	5-16	16	;
	caster		"elves"	5-16	16	;
	healer		"elves"	5-16	16	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	caster	2	"elves"	5-16	16	;
	range		"elves"	5-17	17	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	caster		"elves"	5-16	16	;
	range	2	"elves"	5-17	17	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	caster	2	"elves"	5-16	16	;
	range	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster	2	"elves"	13-25	25	;
}						
						
party						
{						
	caster	2	"elves"	5-16	16	;
	range	2	"elves"	5-17	17	;
}						
						
party						
{						
	caster		"elves"	13-25	25	;
	range	2	"elves"	5-17	17	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	range	2	"elves"	5-17	17	;
	healer		"elves"	5-17	17	;
}						
						
party						
{						
	caster		"elves"	5-17	17	;
	range	2	"elves"	5-17	17	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	healer		"elves"	5-17	17	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	range	2	"elves"	5-17	17	;
	range	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster	2	"elves"	5-16	16	;
	range		"elves"	5-17	17	;
	range	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster		"elves"	13-25	25	;
	range		"elves"	5-17	17	;
	range	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	range	2	"elves"	1-9	9	;
}						
						
party						
{						
	caster		"elves"	5-17	17	;
	range		"elves"	18-30	30	;
	range		"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	5-17	17	;
	range	2	"elves"	5-17	17	;
}						
						
party						
{						
	range		"elves"	18-30	30	;
	range	2	"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range	2	"elves"	5-17	17	;
	healer		"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range	2	"elves"	5-17	17	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	5-17	17	;
	melee	2	"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	5-17	17	;
	melee		"elves_giant"	18-22	22	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	melee	2	"elves"	1-9	9	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	melee	2	"elementals"	7	9	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	18-30	30	;
	melee		"elves"	5-17	17	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	range		"elves"	18-30	30	;
	melee		"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	range		"elves"	5-17	17	;
	melee		"elves"	10-22	22	;
}						
						
party						
{						
	range	2	"elves"	18-30	30	;
}						
						
party						
{						
	range	2	"elves"	5-17	17	;
	melee	2	"elves"	5-17	17	;
}						
						
party						
{						
	range	2	"elves"	5-17	17	;
	melee	2	"elementals"	7	17	;
}						
						
party						
{						
	range	2	"elves"	5-17	17	;
	melee		"elves_giant"	18-22	22	;
}						
						
party						
{						
	range	2	"elves"	1-9	9	;
	melee		"elves_giant"	30	30	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee		"elves_giant"	18-22	22	;
	healer		"elves"	5-17	17	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee	2	"elves"	5-17	17	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee	2	"elementals"	7	17	;
	healer	2	"elves"	1-9	9	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee	2	"elves"	5-17	17	;
	melee	2	"elves"	1-9	9	;
}						
						
party						
{						
	range	2	"elves"	5-17	17	;
	melee		"elves"	5-17	17	;
	melee	2	"elves"	1-9	9	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee		"elves_giant"	18-22	22	;
	melee		"elves"	5-17	17	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee		"elves"	10-22	22	;
	melee	2	"elves"	5-15	15	;
}						
						
party						
{						
	range		"elves"	5-17	17	;
	melee		"elves"	10-22	22	;
	melee	2	"elementals"	7	15	;
}						

rewards "dungeon_lvl_3";
